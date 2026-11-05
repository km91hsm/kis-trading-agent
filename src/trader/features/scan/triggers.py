"""scan 트리거: `news.post_ingest` 훅에서 뉴스·공시가 들어오면 종목 재분석을 큐에 넣고, 새 뉴스가
있으면 유니버스 밖 발굴(큐 kind `discover`)을 요청한다. 옛 데몬 `_news_trigger`·
`_disclosure_trigger`·`_news_reanalyze`·`_discover_body` 의 이식.

경계: 계좌·일시정지·장중 창은 accounts 포트·게이트(`deps.ports["accounts"]`·
`deps.gates["monitor"]`)로만 본다."""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from trader.features.ingest import api as ingest_api
from trader.features.regime import api as regime_api
from trader.features.scan import discovery, messages
from trader.features.scan.jobs import events_check
from trader.features.scan.session_kinds import DISCOVER, agents_for_runner
from trader.features.scan.settings import load_events
from trader.features.scan.watchlist import get_watchlist
from trader.kernel.daemon.queue_jobs import enqueue_from_job
from trader.kernel.execution import horizon
from trader.kernel.execution import ledger as xledger
from trader.kernel.lane.types import LaneContext
from trader.kernel.ledger import control, names
from trader.kernel.notify import messages as kmessages
from trader.kernel.platform import paths
from trader.kernel.session import calls, run_store, usage
from trader.kernel.session.run_store import read_checkpoint, run_date_for
from trader.kernel.session.runner import SessionSpec, is_fake, run_session

logger = logging.getLogger("trader.scan")

DISCLOSURE_SEEN_MAX = 300


def _cache_dir(deps: Any):
    return (deps.state or paths.state_dir()) / "cache"


def session_allowed(deps: Any, conn: sqlite3.Connection, kind: str) -> tuple[bool, str | None]:
    """이 종류의 세션을 지금 띄울 수 있나(구독 사용률 감속). FAKE 는 항상 허용."""
    if is_fake():
        return True, None
    return usage.session_allowance(conn, deps.settings, kind=kind, now=deps.now())


def is_paused(deps: Any, conn: sqlite3.Connection) -> bool:
    """일시정지 여부: accounts 포트의 `paused`. 포트가 없으면 정지 아님."""
    port = (deps.ports or {}).get("accounts")
    return bool(port.paused(conn)) if port is not None else False


def in_market_window(deps: Any, market: str) -> bool:
    """시장의 장중 창 안인가: accounts 가 등록한 `monitor` 게이트가 사유를 내지 않으면 안."""
    gate = (deps.gates or {}).get("monitor")
    if gate is None:
        return True
    return gate(SimpleNamespace(market=market)) is None


def held_tickers(deps: Any, market: str) -> set[str]:
    held: set[str] = set()
    port = (deps.ports or {}).get("accounts")
    if port is None:
        return held
    for b in port.brokers():
        acct = getattr(b, "account", None)
        if acct is not None and market not in acct.markets:
            continue
        try:
            held |= {p.ticker for p in b.get_positions(market)}
        except Exception as e:  # noqa: BLE001
            logger.debug("보유 조회 실패 %s: %s", getattr(acct, "id", "?"), e)
    return held


def _universe_tickers(universe: Any, market: str) -> set[str]:
    return set(universe.kr) if market == "KR" else {u.symbol for u in universe.us}


def exchange_of(deps: Any, conn: sqlite3.Connection, market: str, ticker: str, universe: Any):
    if market != "US":
        return None
    for u in getattr(universe, "us", []) or []:
        if u.symbol == ticker:
            return u.exchange
    return xledger.exchange_of(conn, market, ticker)


def news_reanalyze(
    deps: Any,
    conn: sqlite3.Connection,
    market: str,
    ticker: str,
    items: list[dict],
    today: date,
    universe: Any,
    *,
    exchange: str | None = None,
) -> int | None:
    """새 뉴스가 붙은 종목의 재분석 요청: 오늘 `30_decision` 이 있으면 트리거 kind `news` + 큐
    `reevaluate`(31_decision_r<n>), 없으면 큐 `analyze_ticker`. 중복 요청은 None."""
    exchange = exchange or exchange_of(deps, conn, market, ticker, universe)
    if market == "US" and not exchange:
        logger.warning("뉴스 재분석 생략 %s %s: 거래소 코드 없음", market, ticker)
        return None
    detail = {
        "news": [
            {"id": n.get("id"), "title": n.get("title"), "source": n.get("source")}
            for n in items[:10]
        ],
        "count": len(items),
    }
    did = xledger.decision_id(conn, today, market, ticker)
    if did is not None:
        tid = xledger.insert_trigger(
            conn,
            market=market,
            ticker=ticker,
            kind="news",
            detail=detail,
            price=None,
            decision_id=did,
            account_id=None,
            created_at=deps.now().isoformat(),
        )
        return enqueue_from_job(
            deps,
            "reevaluate",
            {"market": market, "ticker": ticker, "exchange": exchange, "trigger_id": tid},
            source="news",
        )
    if not session_allowed(deps, conn, "analyze")[0]:
        return None  # 감속 중 — 실행 때 막힐 분석을 큐에 넣지 않는다
    params: dict[str, Any] = {"market": market, "ticker": ticker, "dry_run": deps.dry_run}
    if exchange:
        params["exchange"] = exchange
    return enqueue_from_job(deps, "analyze_ticker", params, source="news", priority=5)


def disclosure_trigger(
    deps: Any, conn: sqlite3.Connection, market: str, universe: Any, *, dart=None, edgar=None
) -> dict:
    """공시 중요도(Phase 25 단계 6). 보유·유니버스·관심 종목의 오늘 공시를 적재하고, 처음 보는
    critical 은 `[중요 공시]` 알림 + 새 뉴스 건수 조건(news_min_novel·news_burst) 없이 즉시 재분석
    요청(paused·장중 창 밖이면 알림만). 같은 공시는 `control.disclosure_seen:<market>` 로 1회."""
    disclosures = ingest_api.disclosures
    if dart is None:
        dart = ingest_api.build_dart(deps.secrets)
    if edgar is None:
        sources = ingest_api.load_sources(registry=deps.registry)
        edgar = ingest_api.build_edgar(sources, deps.secrets)
    uni = _universe_tickers(universe, market)
    held = held_tickers(deps, market)
    watch = set(get_watchlist(conn, market=market))
    res = disclosures.collect(
        conn,
        market=market,
        tickers=uni | held | watch,
        now=deps.now(),
        dart=dart,
        edgar=edgar,
    )
    key = f"disclosure_seen:{market}"
    seen = list(control.get_flag(conn, key) or [])
    fresh = [
        r
        for r in disclosures.critical_recent(
            conn, market, since=deps.now() - timedelta(days=1), tickers=uni | held
        )
        if r["id"] not in seen
    ]
    if not fresh:
        return res
    can_run = not is_paused(deps, conn) and in_market_window(deps, market)
    today = run_date_for(market, deps.now())
    cache = _cache_dir(deps)
    hedge = regime_api.excluded_tickers(deps.settings, market)
    queued = []
    for r in fresh:
        for t in r["tickers"]:
            if t in hedge or t not in (uni | held):
                continue
            rid = news_reanalyze(deps, conn, market, t, [r], today, universe) if can_run else None
            action = (
                f"재분석 요청 #{rid}"
                if rid is not None
                else ("재분석 요청 중복" if can_run else "일시정지·장 밖 — 알림만")
            )
            if rid is not None:
                queued.append({"ticker": t, "request_id": rid})
            try:
                deps.notifier.send(
                    messages.disclosure(
                        market=market,
                        label=names.display(conn, cache, market, t),
                        title=r["title"],
                        url=r["url"],
                        action=action,
                    )
                )
            except Exception:  # noqa: BLE001
                logger.debug("중요 공시 알림 실패", exc_info=True)
        seen.append(r["id"])
    control.set_flag(conn, key, seen[-DISCLOSURE_SEEN_MAX:])
    return res | {"critical_new": len(fresh), "queued": queued}


def news_trigger(deps: Any, conn: sqlite3.Connection, market: str, universe: Any) -> dict:
    """장중 뉴스 발굴(Phase 23). 수집 뒤 새 내용 뉴스(`novel_items`)를 판정해 ① 유니버스·보유 종목에
    붙은 새 뉴스 → 그 종목 재분석(오늘 결정 있으면 재평가 트리거 kind `news`, 없으면
    analyze_ticker),
    ② 새 뉴스가 1건 이상이면 scout 세션(큐 kind `discover`)으로 유니버스 밖 발굴. 유사 기사만이면
    아무 세션도 띄우지 않는다. 장중 창·paused·설정이 조건."""
    cfg = deps.settings.discovery.intraday
    key = f"news_trigger_at:{market}"
    since_raw = control.get_flag(conn, key)
    since = datetime.fromisoformat(since_raw) if since_raw else deps.now() - timedelta(hours=1)
    novel = ingest_api.novel_items(
        conn,
        market,
        since=since,
        lookback_days=cfg.similarity.lookback_days,
        jaccard=cfg.similarity.jaccard,
        ratio=cfg.similarity.ratio,
    )
    control.set_flag(conn, key, deps.now().isoformat())
    out: dict[str, Any] = {"novel": len(novel), "queued": [], "discover": None}
    if not novel:
        return out
    if not cfg.enabled:
        return out | {"skipped": "discovery.intraday.enabled=false"}
    if is_paused(deps, conn):
        return out | {"skipped": "일시정지"}
    if not in_market_window(deps, market):
        return out | {"skipped": "장중 창 밖 (아침 scan 이 48시간 뉴스를 본다)"}
    today = run_date_for(market, deps.now())
    uni = _universe_tickers(universe, market)
    held = held_tickers(deps, market)
    burst = discovery.news_burst(novel, universe_tickers=uni, held=held, min_items=cfg.min_items)
    hedge = regime_api.excluded_tickers(deps.settings, market)
    for ticker, items in burst["tickers"].items():
        if ticker in hedge:
            continue
        if ticker in held:  # 보유 종목은 보유기간 프로필의 news_min_novel 을 본다(Phase 23 T)
            latest = xledger.latest_decision(conn, market, ticker)
            _, prof = horizon.of_decision(deps.settings.risk, (latest or {}).get("data"))
            if len(items) < prof.news_min_novel:
                out.setdefault("below_profile", []).append(
                    {"ticker": ticker, "news": len(items), "need": prof.news_min_novel}
                )
                continue
        rid = news_reanalyze(deps, conn, market, ticker, items, today, universe)
        if rid is not None:
            out["queued"].append({"ticker": ticker, "request_id": rid, "news": len(items)})
    cap_key = f"discover_count:{market}:{today.isoformat()}"
    n_disc = int(control.get_flag(conn, cap_key) or 0)
    allowed, why = session_allowed(deps, conn, "discover")
    if not allowed:  # 감속 중엔 큐에 넣지 않는다(넣으면 실행 때 막혀 '오류'로 쌓였다, 2026-10-29)
        out["discover"] = f"사용량 감속 — {why}"
    elif n_disc < cfg.discover_cap:
        rid = enqueue_from_job(deps, "discover", {"market": market}, source="news")
        if rid is None:
            out["discover"] = "pending"
        else:  # 상한은 실제로 큐에 들어간 요청만 센다(Review Focus 3)
            control.set_flag(conn, cap_key, n_disc + 1)
            out["discover"] = rid
    else:
        out["discover"] = f"상한 {cfg.discover_cap}"
    if out["queued"] or isinstance(out["discover"], int):
        cache = _cache_dir(deps)
        parts = [
            f"{names.display(conn, cache, market, q['ticker'])} 새 뉴스 {q['news']}건"
            f" → #{q['request_id']}"
            for q in out["queued"]
        ]
        if isinstance(out["discover"], int):
            parts.append(f"유니버스 밖 발굴 세션 #{out['discover']}")
        try:
            deps.notifier.send(
                kmessages.event("뉴스 발굴", f"{market} 새 뉴스 {len(novel)}건", *parts)
            )
        except Exception:  # noqa: BLE001
            logger.debug("뉴스 발굴 알림 실패", exc_info=True)
    return out


def post_ingest(
    *,
    conn: sqlite3.Connection,
    markets: Any,
    now: datetime,
    result: dict | None = None,
    notifier: Any = None,
    deps: Any = None,
    **_: Any,
) -> dict:
    """`news.post_ingest` 훅: 시장별 사건 감지 → (데몬 문맥이 있으면) 공시·뉴스 트리거. 시장·
    트리거 하나의 실패는 그 항목만 error 로 남긴다."""
    cfg = load_events()
    out: dict[str, dict] = {}
    for market in markets:
        try:
            out[f"events:{market}"] = events_check(
                conn, market, cfg=cfg, now=now, notifier=notifier
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("사건 감지 실패 %s: %s", market, e)
            out[f"events:{market}"] = {"error": f"{type(e).__name__}: {e}"}
    if deps is None:
        return out
    universe = ingest_api.load_universe()
    for market in markets:
        for name, fn in (("disclosure", disclosure_trigger), ("news", news_trigger)):
            try:
                out[f"{name}:{market}"] = fn(deps, conn, market, universe)
            except Exception as e:  # noqa: BLE001
                logger.warning("%s 트리거 실패 %s: %s", name, market, e)
                out[f"{name}:{market}"] = {"error": f"{type(e).__name__}: {e}"}
    return out


def discover_request(deps: Any, params: dict, progress: Any) -> dict:
    """큐 `discover` 본문(옛 `_discover_body`, Phase 23): 새 내용 뉴스만 실은 발굴 입력 → scout
    1단계 세션(kind=discover) → `_discovery` 후보를 재분석 큐로(오늘 결정 있으면 재평가). 세션
    상한은 `run_session` 이 본다."""
    conn = deps.conn_factory()
    try:  # 세션 전에 stale running(1시간 초과) 세션 행을 error 로 닫는다
        calls.close_stale(conn, now=deps.now())
    finally:
        conn.close()
    market = params["market"]
    universe = ingest_api.load_universe()
    today = run_date_for(market, deps.now())
    conn = deps.conn_factory()
    try:
        lo = (deps.now() - timedelta(hours=6)).astimezone(UTC).isoformat()
        rows = conn.execute(
            "SELECT id, market, source, query, title, url, url_hash, published_at, received_at, "
            "summary, tickers_json, lang FROM news_items WHERE market=? AND novel=1 "
            "AND received_at >= ? ORDER BY received_at DESC LIMIT ?",
            (market, lo, 60),
        ).fetchall()
        novel = [ingest_api.row_to_dict(r) for r in rows]
        if not novel:
            return {"skipped": "새 뉴스 없음"}
        analyzed = [
            r["ticker"]
            for r in conn.execute(
                "SELECT ticker FROM decisions WHERE date=? AND market=?",
                (today.isoformat(), market),
            ).fetchall()
        ]
        held = sorted(held_tickers(deps, market))
        try:
            sources_cfg = ingest_api.load_sources(registry=deps.registry)
            news_cfg = sources_cfg.news
        except Exception:  # noqa: BLE001
            sources_cfg = news_cfg = None
        with ingest_api.owned_client(None, timeout=15) as http:
            discovery.build_inputs(
                conn,
                deps.settings,
                universe,
                market=market,
                today=today,
                asof=deps.now(),
                quant_candidates=[],
                held=held,
                state=deps.state,
                now=deps.now,
                news_cfg=news_cfg,
                cache_names=names.load_cache_names(_cache_dir(deps)),
                sources_cfg=sources_cfg,
                http=http,
                intraday_news=novel,
                analyzed_today=analyzed,
            )
        ctx = LaneContext(
            settings=deps.settings,
            conn=conn,
            state=deps.state,
            now=deps.now,
            dry_run=deps.dry_run,
            notifier=deps.notifier,
            ports=deps.ports,
        )
        res = run_session(
            SessionSpec(
                market=market,
                date=today,
                tickers=[],
                dry_run=True,
                asof=deps.now(),
                kind="discover",
            ),
            DISCOVER,
            ctx,
            agents=agents_for_runner(),
            cancel=deps.stop_event,
        )
        if not res.ok:
            return {"error": f"discover 세션 실패: {res.error}", "novel": len(novel)}
        found = (
            read_checkpoint(run_store.market_dir(today, market, deps.state), "_discovery") or {}
        ).get("data") or {}
        hedge = regime_api.excluded_tickers(deps.settings, market)
        queued = []
        for c in found.get("candidates") or []:
            t = c.get("ticker")
            if not t or t in hedge:
                continue
            rid = news_reanalyze(
                deps, conn, market, t, [], today, universe, exchange=c.get("exchange")
            )
            if rid is not None:
                queued.append({"ticker": t, "request_id": rid, "tier": c.get("tier")})
        if queued:
            deps.notifier.send(
                kmessages.event(
                    "장중 발굴",
                    market,
                    *(f"{q['ticker']}({q['tier']}) → 요청 #{q['request_id']}" for q in queued),
                )
            )
        return {
            "novel": len(novel),
            "candidates": len(found.get("candidates") or []),
            "queued": queued,
        }
    finally:
        conn.close()
