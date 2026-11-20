"""세이브티커 로컬 수집 폴더 리더 (Phase 19 이연 회수).

세이브티커(saveticker.com)는 Cloudflare 봇 검증 + robots AI 크롤러 금지라 이 저장소는 사이트를 긁지
않는다. 대신 사용자의 별도 수집기가 **이 프로젝트의 `state/external/saveticker/`** 에 쌓는 파일을
읽기만 한다(`sources.yaml saveticker.dir`, 폴더가 없으면 조용히 건너뜀). 구조(수집기 v1):

- `data/<YYYY-MM-DD>/{news,community,market,calendar}.jsonl` — 한 줄 = 변경 이력 하나
  `{revision, stream, received_at(UTC), item, view}`
- `data/latest/<stream>.json` — 스트림별 최신 화면 `{stream, observed_at, status, items, view}`
- `data/attachments/<sha>.pdf|.txt` — SAVE 데일리 리포트 원본·추출 텍스트

뉴스(`news_all`·`news_reports`)는 시장 전체 뉴스 저장소(`news_items`, source `saveticker:<출처>`)에
멱등 적재되어 발굴·시장 세션·종목 세션이 다른 뉴스와 같은 길로 읽는다. 종목 TOP5·지수·상승/하락/
거래대금·캘린더·리포트 발췌는 `snapshot(asof)` 로 세션 입력(`saveticker` 절)에 들어간다(커뮤니티
트렌드는 2026-10-26 수집 중단이라 읽지 않는다).
룩어헤드: `observed_at`/`received_at` 이 asof 이하인 것만. 신선도: latest 화면은 수집기가 매 회차
`observed_at` 을 새로 쓰므로, asof 보다 `STALE_AFTER` 넘게 오래된 화면은 수집기가 멈춘 것으로 보고
뺀다(`snapshot.stale` 에 스트림 이름).
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from trader.features.saveticker.dom import identity
from trader.features.saveticker.paths import DEFAULT_DIR as DEFAULT_DIR  # 재노출
from trader.features.saveticker.paths import resolve_dir as resolve_dir  # 재노출
from trader.features.saveticker.settings import load_cfg
from trader.kernel.config import KST
from trader.kernel.config.errors import ConfigError

logger = logging.getLogger("trader.features.saveticker")

NEWS_STREAMS = ("news_all", "news_reports")
REPORT_EXCERPT_CHARS = 1500
SUMMARY_CHARS = 300
JSONL_DAYS = 3  # 뉴스 적재는 최근 N일 폴더만 다시 읽는다(URL 해시로 멱등)
CALENDAR_BACK_DAYS = 1
CALENDAR_AHEAD_DAYS = 7
# 화면 신선도: 순위·TOP5 는 20초~5분 주기라 1시간, 리포트(15분)는 날 단위라 하루, 캘린더는 예정
# 일정이라 오래된 관측도 쓸모가 있어 2주
STALE_AFTER = timedelta(hours=1)
STALE_AFTER_BY_PREFIX = (("calendar_", timedelta(days=14)), ("news_reports", timedelta(days=1)))
_KR_CODE = re.compile(r"[0-9]{6}|[A-Z0-9]{6}\.KR")
_DISPLAYED = re.compile(r"^(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{1,2}):(\d{2})$")


def available(root: Path | None) -> bool:
    return bool(root) and (root / "data").is_dir()  # type: ignore[operator]


def _parse_dt(s: Any) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def published_at_of(item: dict) -> str | None:
    """`published_at`(UTC) 우선, 없으면 화면 시각 '2026. 10. 24. 07:40'(KST).
    상대시각('2시간 전')만 있으면 None."""
    dt = _parse_dt(item.get("published_at"))
    if dt is not None:
        return dt.isoformat()
    shown = str(item.get("displayed_time") or "").strip()
    m = _DISPLAYED.match(shown)
    if m:
        y, mo, d, hh, mm = (int(x) for x in m.groups())
        try:
            return datetime(y, mo, d, hh, mm, tzinfo=KST).astimezone(UTC).isoformat()
        except ValueError:
            return None
    iso = _parse_dt(shown)
    return iso.isoformat() if iso else None


def kr_ticker_code(ticker: str) -> str | None:
    """세이브티커 국내 종목 태그를 조회용 코드로 바꾼다."""
    symbol = ticker.strip().upper()
    return symbol.removesuffix(".KR") if _KR_CODE.fullmatch(symbol) else None


def tickers_by_market(item: dict) -> dict[str, list[str]]:
    """원문 티커를 시장별 조회 코드로 바꾼다. 티커 없는 거시·속보는 두 시장에 싣는다."""
    codes: dict[str, set[str]] = {"KR": set(), "US": set()}
    for value in item.get("tickers") or []:
        ticker = str(value or "").strip().upper()
        if not ticker:
            continue
        if kr_code := kr_ticker_code(ticker):
            codes["KR"].add(kr_code)
        else:
            codes["US"].add(ticker)
    return {m: sorted(ts) for m, ts in codes.items() if ts} or {"KR": [], "US": []}


def markets_of(item: dict) -> list[str]:
    """기사가 다루는 시장을 KR·US 순으로 돌려준다."""
    return list(tickers_by_market(item))


def report_excerpt(root: Path, item: dict, limit: int = REPORT_EXCERPT_CHARS) -> str | None:
    """리포트 첨부 추출 텍스트의 앞부분(공백 접음). 첨부가 없거나 `data/` 밖이면 None."""
    att = item.get("attachment") or {}
    rel = att.get("text_path")
    if not rel:
        return None
    p = (root / "data" / rel).resolve()
    if (root / "data").resolve() not in p.parents or not p.is_file():
        return None
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # 추출 텍스트는 토큰 단위 줄바꿈이 많다 → 공백으로 접는다
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _jsonl_dates(root: Path, *, today: date, days: int) -> list[Path]:
    out = []
    for i in range(-1, days):  # 수집 PC 가 KST 자정을 먼저 넘기면 '내일' 폴더가 있을 수 있다
        d = root / "data" / (today - timedelta(days=i)).isoformat()
        if d.is_dir():
            out.append(d)
    return out


def read_news_items(root: Path, *, today: date, days: int = JSONL_DAYS) -> list[dict]:
    """최근 N일 `news.jsonl` 의 뉴스·리포트 항목(같은 id 는 마지막 revision 만).
    `_received_at`·`_stream`·`_revision` 을 덧붙인다."""
    latest: dict[str, dict] = {}
    for d in _jsonl_dates(root, today=today, days=days):
        p = d / "news.jsonl"
        if not p.is_file():
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as e:
            logger.warning("세이브티커 파일 읽기 실패 %s: %s", p, e)
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("stream") not in NEWS_STREAMS:
                continue
            item = row.get("item") or {}
            iid = str(item.get("id") or "")
            if not iid:
                continue
            prev = latest.get(iid)
            if prev is None or int(row.get("revision") or 0) >= int(prev.get("_revision") or 0):
                latest[iid] = {
                    **item,
                    "_revision": row.get("revision"),
                    "_received_at": row.get("received_at"),
                    "_stream": row.get("stream"),
                }
    return list(latest.values())


def read_completed_news(root: Path, *, since: datetime, asof: datetime) -> list[dict]:
    """수신 시각 범위에서 본문이 확인된 기사와 제목 전용 속보를 읽는다."""
    path = root / "data" / "feed.sqlite3"
    if not path.is_file():
        return []
    bounds = (since.astimezone(UTC).isoformat(), asof.astimezone(UTC).isoformat())
    latest = {}
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
        for stamp, payload in db.execute(
            "SELECT received_at, payload FROM revisions WHERE stream='news_all' "
            "AND received_at BETWEEN ? AND ? AND json_extract(payload,'$.detail_status')='ok' "
            "ORDER BY received_at, seq",
            bounds,
        ):
            item = json.loads(payload)
            latest[item["id"]] = {**item, "_stream": "news_all", "_received_at": stamp}
    return list(latest.values())


def _feed_rows(sql: str, params: tuple) -> list[tuple]:
    try:
        root = resolve_dir(load_cfg().dir)
        if root is None or not (path := root / "data" / "feed.sqlite3").is_file():
            return []
        uri = path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=1)) as db:
            return db.execute(sql, params).fetchall()
    except (OSError, sqlite3.Error, ConfigError):
        return []  # 수집 DB가 없거나 읽을 수 없어도 개별 기사 제목을 표시한다.


def read_group_headlines(items: list[dict], *, asof: datetime) -> dict[int, str]:
    """목록에 있는 기사별로 마지막 관측에 기록된 묶음 제목을 조회한다."""
    wanted = []
    item_ids = []
    for row in items:
        if row["query"] != "news_all" or not row["source"].startswith("saveticker:"):
            continue
        publisher = row["source"].removeprefix("saveticker:")
        try:
            link = urlsplit(row["url"])
            if (
                link.scheme not in {"http", "https"}
                or not link.hostname
                or link.username
                or link.password
            ):
                continue
            synthetic = (
                link.hostname in {"saveticker.com", "www.saveticker.com"}
                and link.path.rstrip("/") == "/news"
                and not link.query
                and link.fragment
            )
        except ValueError:
            continue
        item_ids.append(link.fragment if synthetic else identity("news", publisher, row["title"]))
        wanted.append((row, publisher, None if synthetic else row["url"]))
    if not item_ids:
        return {}
    cutoff = asof.astimezone(UTC)
    rows = _feed_rows(
        """SELECT w.key, json_extract(r.payload,'$.group_headline'),
                  COALESCE(NULLIF(json_extract(r.payload,'$.publisher'), ''),
                           NULLIF(json_extract(r.payload,'$.source_group'), ''), 'saveticker'),
                  json_extract(r.payload,'$.published_at'),
                  json_extract(r.payload,'$.displayed_time'), json_extract(r.payload,'$.source_url')
           FROM json_each(?) AS w JOIN revisions AS r ON r.seq=(
               SELECT seq FROM revisions
               WHERE stream='news_all' AND item_id=w.value AND received_at<=?
               ORDER BY received_at DESC, seq DESC LIMIT 1
           ) WHERE json_extract(r.payload,'$.kind')='news'
               AND json_type(r.payload,'$.group_headline')='text'""",
        (json.dumps(item_ids), cutoff.isoformat()),
    )
    headlines = {}
    for index, headline, issuer, published, displayed, url in rows:
        row, publisher, source_url = wanted[index]
        if publisher != issuer.strip() or (source_url and url and source_url != url):
            continue
        observed_pub = _parse_dt(
            published_at_of({"published_at": published, "displayed_time": displayed})
        )
        expected_pub = _parse_dt(row["published_at"])
        if (expected_pub and expected_pub != observed_pub) or (
            observed_pub and observed_pub > cutoff
        ):
            continue
        if headline.strip():
            headlines[row["id"]] = headline.strip()
    return headlines


def news_revision(*, asof: datetime) -> int:
    """묶음 제목만 바뀌어도 뉴스 목록이 새로 읽도록 수집 리비전을 돌려준다."""
    rows = _feed_rows(
        "SELECT seq FROM revisions INDEXED BY revisions_news_received "
        "WHERE stream='news_all' AND received_at<=? "
        "ORDER BY received_at DESC, seq DESC LIMIT 1",
        (asof.astimezone(UTC).isoformat(),),
    )
    return rows[0][0] if rows else 0


# --- 스냅샷(세션 입력) -------------------------------------------------------------------


SCHEMA_VERSION = (
    1  # 사용자 수집기의 파일 schema_version. 다르면 경고 1회(프로세스) 뒤 그대로 읽는다
)
_schema_warned: set[int] = set()


def _check_schema(snap: dict | None, where: str) -> None:
    v = (snap or {}).get("schema_version")
    if v is None or v == SCHEMA_VERSION or v in _schema_warned:
        return
    _schema_warned.add(v)
    logger.warning(
        "세이브티커 schema_version %s (리더는 %s 기준) %s — 필드가 달라졌을 수 있어 리더를 맞춘다",
        v,
        SCHEMA_VERSION,
        where,
    )


def _load_latest(root: Path, stream: str) -> dict | None:
    p = root / "data" / "latest" / f"{stream}.json"
    if not p.is_file():
        return None
    try:
        snap = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("세이브티커 latest 읽기 실패 %s: %s", p.name, e)
        return None
    _check_schema(snap, p.name)
    return snap


def _calendar(
    root: Path,
    cutoff: datetime,
    *,
    back_days: int,
    ahead_days: int,
    stale: set[str] | None = None,
) -> list[dict]:
    """경제 일정(월 파일 여러 개) 중 cutoff-back ~ cutoff+ahead 창, scheduled_at 오름차순."""
    lo = cutoff - timedelta(days=back_days)
    hi = cutoff + timedelta(days=ahead_days)
    span = (hi - lo).days + 2
    months = sorted({(lo + timedelta(days=i)).strftime("%Y-%m") for i in range(0, span)})
    out: list[dict] = []
    for ym in months:
        for it in _items(_load_latest(root, f"calendar_{ym}"), cutoff, stale):
            when = _parse_dt(it.get("scheduled_at"))
            if when is None or not (lo <= when <= hi):
                continue
            out.append(
                {
                    "scheduled_at": when.isoformat(),
                    "title": it.get("title"),
                    "actual": it.get("actual"),
                    "forecast": it.get("forecast"),
                    "previous": it.get("previous"),
                }
            )
    out.sort(key=lambda r: r["scheduled_at"])
    return out


def upcoming(root: Path | None, *, asof: datetime, days: int = 7) -> list[dict]:
    """종목 세션 `news_macro.events.upcoming` 용: asof ~ +days 의 경제 일정(세이브티커 캘린더).
    폴더가 없으면 빈 목록. 항목은 {date(KST), kind: "macro", title, forecast, previous, source}."""
    if root is None or not available(root):
        return []
    cutoff = asof.astimezone(UTC)
    out = []
    for c in _calendar(root, cutoff, back_days=0, ahead_days=days):
        when = _parse_dt(c["scheduled_at"])
        if when is None or when < cutoff:
            continue
        out.append(
            {
                "date": when.astimezone(KST).strftime("%Y-%m-%d %H:%M"),
                "kind": "macro",
                "title": c.get("title"),
                "forecast": c.get("forecast"),
                "previous": c.get("previous"),
                "source": "saveticker",
            }
        )
    return out


def _items(snap: dict | None, asof: datetime, stale: set[str] | None = None) -> list[dict]:
    if not snap:
        return []
    observed = _parse_dt(snap.get("observed_at"))
    cutoff = asof.astimezone(UTC)
    if observed is not None and observed > cutoff:
        return []  # asof 뒤 화면은 보이지 않는다(룩어헤드)
    stream = str(snap.get("stream") or "")
    limit = next((v for k, v in STALE_AFTER_BY_PREFIX if stream.startswith(k)), STALE_AFTER)
    if observed is not None and cutoff - observed > limit:
        if stale is not None:
            stale.add(stream)
        return []  # 수집기가 멈춘 뒤 남은 화면을 '지금' 값으로 쓰지 않는다
    return list(snap.get("items") or [])


def _quote_rows(items: list[dict], *, with_turnover: bool = False) -> list[dict]:
    out = []
    for it in items:
        row = {
            "rank": it.get("rank"),
            "name": it.get("title"),
            "tickers": list(it.get("tickers") or []),
            "change_pct": it.get("change_pct"),
            "price": it.get("price"),
            "basis": it.get("basis"),
        }
        if with_turnover:
            row["turnover"] = it.get("turnover")
        if it.get("views"):
            row["views"] = it.get("views")
        out.append(row)
    return out


def snapshot(root: Path | None, *, asof: datetime, market: str | None = None) -> dict | None:
    """세션 입력용 요약(토큰 절약: 항목 수 그대로, 필드만 고른다). 폴더가 없으면 None."""
    if root is None or not available(root):
        return None
    cutoff = asof.astimezone(UTC)
    stale: set[str] = set()

    def latest(stream: str) -> list[dict]:
        return _items(_load_latest(root, stream), cutoff, stale)

    index = _quote_rows(latest("community_index_regular"))
    top5 = _quote_rows(latest("community_top5"))
    movers = {
        key: _quote_rows(latest(f"market_{key}_regular"), with_turnover=True)
        for key in ("gain", "loss", "turnover")
    }
    community = {key: _quote_rows(latest(f"community_{key}_regular")) for key in ("gain", "loss")}
    # 캘린더: asof 기준 -1일 ~ +7일 일정 (월 파일 여러 개)
    calendar = _calendar(
        root, cutoff, back_days=CALENDAR_BACK_DAYS, ahead_days=CALENDAR_AHEAD_DAYS, stale=stale
    )
    report = None
    reports = latest("news_reports")
    if reports:
        latest_r = max(reports, key=lambda r: str(r.get("report_date") or ""))
        report = {
            "report_date": latest_r.get("report_date"),
            "title": latest_r.get("title"),
            "excerpt": report_excerpt(root, latest_r) or "",
        }
    status = _load_latest(root, "../status") or {}
    observed = [
        _parse_dt(s.get("observed_at"))
        for s in (
            _load_latest(root, "community_top5"),
            _load_latest(root, "market_gain_regular"),
            _load_latest(root, "news_all"),
        )
        if s
    ]
    observed = [o for o in observed if o is not None and o <= cutoff]
    result = {
        "source": "saveticker (로컬 수집기, saveticker.dir)",
        "observed_at": max(observed).isoformat() if observed else None,
        "index": index,
        "community_top5": top5,
        "community": community,
        "movers": movers,
        "calendar": calendar,
        "report": report,
        "stale": sorted(stale) or None,
        "streams_status": {
            k: v.get("status")
            for k, v in (status.get("streams") or {}).items()
            if isinstance(v, dict) and k != "community_trends"
        }
        or None,
    }

    gaps = [f"stale:{stream}" for stream in sorted(stale)]
    gaps.extend(
        f"stream:{k}:{v}"
        for k, v in (result["streams_status"] or {}).items()
        if v not in ("ok", "success")
    )
    if market is not None:
        result["market"] = market
        # These ranking streams cover US stocks. Unknown market must not imply KR.
        if market != "US":
            result["global_context"] = {
                "market": "US",
                "scope": "global_reference",
                "index": result["index"],
            }
            result["index"] = []
            result["movers"] = {key: [] for key in movers}
            result["community"] = {key: [] for key in community}
            result["community_top5"] = []
            gaps.append("saveticker rankings cover US; KR coverage unavailable")
    result["data_gaps"] = gaps
    return result


def for_ticker(snap: dict | None, ticker: str, *, market: str | None = None) -> dict | None:
    """종목 세션 분위기 절: 이 종목이 등장하는 TOP5·상승/하락·거래대금 행 + 시장 지수."""
    if not snap:
        return None
    if market is not None and snap.get("market") != market:
        snap = {**snap, "market": market}
        if market != "US":
            snap["global_context"] = {
                "market": "US",
                "scope": "global_reference",
                "index": snap.get("index") or [],
            }
            snap["index"] = []
            snap["movers"] = {}
            snap["community"] = {}
            snap["community_top5"] = []
            snap["data_gaps"] = [*(snap.get("data_gaps") or []), "KR rankings unavailable"]
    t = ticker.upper()

    def hits(rows: list[dict]) -> list[dict]:
        return [r for r in rows if t in [str(x).upper() for x in r.get("tickers") or []]]

    mentions = {
        "community_top5": hits(snap.get("community_top5") or []),
        "community_gain": hits((snap.get("community") or {}).get("gain") or []),
        "community_loss": hits((snap.get("community") or {}).get("loss") or []),
        "market_gain": hits((snap.get("movers") or {}).get("gain") or []),
        "market_loss": hits((snap.get("movers") or {}).get("loss") or []),
        "market_turnover": hits((snap.get("movers") or {}).get("turnover") or []),
    }
    return {
        **{
            k: snap.get(k)
            for k in ("source", "market", "stale", "streams_status", "data_gaps", "global_context")
        },
        "observed_at": snap.get("observed_at"),
        "mentions": {k: v for k, v in mentions.items() if v},
        "index": snap.get("index") or [],
        "note": (
            "mentions가 비어 있어도 미수집·지연·부분수집일 수 있어 순위 미진입을 뜻하지 않는다. "
            "stale·streams_status·data_gaps와 시장 범위를 확인한다"
        ),
    }
