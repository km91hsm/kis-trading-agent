"""SQLite 변경 이력과 복구 가능한 JSONL·최신 파일을 저장한다."""

from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
import sqlite3
import tempfile
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from trader.features.saveticker.api_contract import MAX_NEWS_PAGE, NEWS_PAGE_SIZE
from trader.features.saveticker.dom import identity
from trader.features.saveticker.models import Capture, Item

# 뉴스의 참여 수치(조회수 등)는 20초마다 바뀌어 같은 기사가 하루 수백 번 새 버전이 됐다 — 뉴스
# 스트림에서는 디지스트에서 뺀다(커뮤니티·순위 화면은 그 숫자가 정보라 그대로 둔다).
_VOLATILE = ("displayed_time",)
_NEWS_VOLATILE = ("views", "likes", "comment_count")


def _item_digest(stream: str, item: dict) -> str:
    drop = _VOLATILE + (_NEWS_VOLATILE if stream.startswith("news_") else ())
    stable = {k: v for k, v in item.items() if k not in drop}
    return hashlib.sha256(_json(stable).encode()).hexdigest()


_GOOD = (
    "json_extract(payload,'$.status') IN ('ok','partial') "
    "AND json_array_length(payload,'$.items') > 0"
)
_LAST_GOOD_SQL = (
    f"SELECT payload,seq FROM captures WHERE stream=? AND {_GOOD} ORDER BY seq DESC LIMIT 1"
)

_CACHED_NEWS_DETAIL_SQL = (
    "SELECT payload FROM revisions WHERE stream='news_all' AND item_id=? AND received_at<=? "
    "AND json_extract(payload,'$.kind')='news' "
    "AND COALESCE(json_extract(payload,'$.published_at'), received_at)<=? "
    "AND (? IS NULL OR json_extract(payload,'$.published_at')=?) "
    "AND json_extract(payload,'$.detail_status')='ok' AND TRIM(json_extract(payload,'$.body'))<>'' "
    "ORDER BY seq DESC LIMIT 1"
)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def atomic_json(path: Path, value) -> None:
    atomic_text(path, _json(value) + "\n")


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
    ) as f:
        tmp = Path(f.name)
        try:
            f.write(text)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    try:
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] > 1:
                raise ValueError("더 새로운 수집 DB 형식입니다")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS revisions (
                    seq INTEGER PRIMARY KEY, stream TEXT NOT NULL, item_id TEXT NOT NULL,
                    received_at TEXT NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL,
                    view TEXT NOT NULL DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS revisions_lookup
                    ON revisions(stream, item_id, seq);
                CREATE INDEX IF NOT EXISTS revisions_news_received
                    ON revisions(received_at, seq) WHERE stream='news_all';
                CREATE INDEX IF NOT EXISTS revisions_news_item_received
                    ON revisions(item_id, received_at, seq) WHERE stream='news_all';
                CREATE TABLE IF NOT EXISTS legacy_news_ids (
                    source_id TEXT PRIMARY KEY, item_id TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS news_head (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    capture_seq INTEGER NOT NULL, source_ids TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS news_gap (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), anchor TEXT NOT NULL,
                    page INTEGER NOT NULL, retry_at TEXT NOT NULL, started_at TEXT NOT NULL,
                    last_ids TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS exports (
                    path TEXT PRIMARY KEY, last_seq INTEGER NOT NULL, byte_count INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS captures (
                    seq INTEGER PRIMARY KEY, stream TEXT NOT NULL,
                    observed_at TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS captures_stream_seq ON captures(stream, seq);
            """)
            db.execute("BEGIN IMMEDIATE")
            columns = {row[1] for row in db.execute("PRAGMA table_info(revisions)")}
            if "view" not in columns:
                db.execute("ALTER TABLE revisions ADD COLUMN view TEXT NOT NULL DEFAULT '{}' ")
            db.execute("PRAGMA user_version=1")

    @contextlib.contextmanager
    def _connect(self, timeout: float = 30):
        db = sqlite3.connect(self.root / "feed.sqlite3", timeout=timeout)
        db.execute("PRAGMA journal_mode=WAL")
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _capture_data(capture):
        data = capture.model_dump(mode="json")
        data["observed_at"] = capture.observed_at.isoformat()
        for item in data["items"]:
            if item["group_headline"] is None:
                # 구버전과 같은 항목의 디지스트를 유지한다.
                del item["group_headline"]
            for key in ("published_at", "scheduled_at"):
                if item.get(key):
                    item[key] = datetime.fromisoformat(item[key]).astimezone(UTC).isoformat()
        return data

    @staticmethod
    def _write_revisions(db, data, *, only_new=False):
        changed = 0
        for item in data["items"]:
            prior = db.execute(
                "SELECT seq,digest FROM revisions WHERE stream=? AND item_id=? "
                "ORDER BY seq DESC LIMIT 1",
                (data["stream"], item["id"]),
            ).fetchone()
            digest = _item_digest(data["stream"], item)
            if prior and prior["digest"] == digest:
                continue
            if prior and only_new:
                headline = item.get("group_headline")
                # 과거 목록의 상세는 생략된다. 묶음 제목이 달라진 경우만 기존 본문을 읽는다.
                previous = db.execute(
                    "SELECT payload FROM revisions WHERE seq=? "
                    "AND json_extract(payload,'$.group_headline') IS NOT ?",
                    (prior["seq"], headline),
                ).fetchone()
                if not previous:
                    continue
                item = json.loads(previous["payload"])
                if headline is None:
                    item.pop("group_headline", None)
                else:
                    item["group_headline"] = headline
                digest = _item_digest(data["stream"], item)
            db.execute(
                "INSERT INTO revisions(stream,item_id,received_at,digest,payload,view) "
                "VALUES (?,?,?,?,?,?)",
                (
                    data["stream"],
                    item["id"],
                    data["observed_at"],
                    digest,
                    _json(item),
                    _json(data["view"]),
                ),
            )
            changed += 1
        return changed

    def save(
        self, capture: Capture, *, news_pages: bool | None = None, total_news=None, source_ids=()
    ) -> dict:
        data = self._capture_data(capture)
        stamp = data["observed_at"]
        changed = 0
        page = None
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # 같은 스트림의 관측 시각 역행은 최신 파일과 PIT 이력을 오염시키므로 거부한다.
            last = db.execute(
                "SELECT observed_at FROM captures WHERE stream=? ORDER BY seq DESC LIMIT 1",
                (capture.stream,),
            ).fetchone()
            if last and stamp < last[0]:
                raise ValueError("수집 시각이 이전 관측보다 과거입니다")
            if capture.status in ("ok", "partial"):
                changed = self._write_revisions(db, data)
            if capture.stream == "news_all" and news_pages is not None:
                page = self._check_news_gap(db, data, news_pages, total_news, source_ids)
            inserted = db.execute(
                "INSERT INTO captures(stream,observed_at,payload) VALUES (?,?,?)",
                (capture.stream, stamp, _json(data)),
            )
            if news_pages is not None and source_ids:
                db.execute(
                    "INSERT OR REPLACE INTO news_head VALUES (1,?,?)",
                    (inserted.lastrowid, _json(sorted(source_ids))),
                )
        self.export(incremental=True)
        return {
            **({"news_page": page} if page is not None else {}),
            "stream": capture.stream,
            "changed": changed,
            "count": len(capture.items),
            "status": capture.status,
        }

    @staticmethod
    def _check_news_gap(db, head, allow_request, total_news, source_ids):
        """앞쪽 저장과 함께 공백을 기록한다. 실패해도 재접속 시 같은 쪽부터 이어간다."""
        stamp = head["observed_at"]
        cutoff = (datetime.fromisoformat(stamp) - timedelta(days=2)).isoformat()
        db.execute("DELETE FROM news_gap WHERE started_at < ?", (cutoff,))
        items = head["items"]
        if total_news is not None and total_news <= NEWS_PAGE_SIZE:
            db.execute("DELETE FROM news_gap")
            return None
        if len(items) < NEWS_PAGE_SIZE:
            # 구버전의 총건수 없는 짧은 목록으로 기존 공백이 끝났다고 추정하지 않는다.
            return None
        ids = {i["id"] for i in items}.union(source_ids)
        last = db.execute(_LAST_GOOD_SQL, ("news_all",)).fetchone()
        if last:
            sources = db.execute(
                "SELECT source_ids FROM news_head WHERE singleton=1 AND capture_seq=?",
                (last["seq"],),
            ).fetchone()
            anchor = (
                set(json.loads(sources[0]))
                if sources else {i["id"] for i in json.loads(last["payload"])["items"]}
            )
            if anchor.isdisjoint(ids):
                # 진행 중에 앞쪽이 다시 단절되면 새 구간도 읽되 최초 연결 대상은 보존한다.
                db.execute(
                    "INSERT INTO news_gap VALUES(1,?,2,'',?,'[]') "
                    "ON CONFLICT(singleton) DO UPDATE SET page=2,last_ids='[]'",
                    (_json(sorted(anchor)), stamp),
                )
        gap = db.execute("SELECT page,retry_at,anchor FROM news_gap").fetchone()
        if not gap:
            return None
        if not ids.isdisjoint(json.loads(gap["anchor"])):
            db.execute("DELETE FROM news_gap")
            return None
        if not allow_request or gap["retry_at"] > stamp:
            return None
        # 요청 전에 대기 시간을 남겨 확장·Native 중단도 즉시 재시도 폭주로 이어지지 않게 한다.
        retry_at = (datetime.fromisoformat(stamp) + timedelta(minutes=5)).isoformat()
        db.execute("UPDATE news_gap SET retry_at=?", (retry_at,))
        return gap["page"]

    def save_news_page(self, capture, *, page, total_news, more_this_cycle, source_ids, head_ids):
        """과거 목록의 신규 기사와 다음 위치를 한 번에 저장한다. 최신 캡처는 첫 쪽이다."""
        data = self._capture_data(capture)
        ids = {i["id"] for i in data["items"]}.union(source_ids)
        count = len(data["items"])
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            gap = db.execute("SELECT * FROM news_gap WHERE page=?", (page,)).fetchone()
            if not gap:
                raise ValueError("invalid_job")
            if source_ids and (
                source_ids == head_ids or source_ids == set(json.loads(gap["last_ids"]))
            ):
                raise ValueError("api_shape_unverified")
            self._write_revisions(db, data, only_new=True)
            if (
                not ids.isdisjoint(json.loads(gap["anchor"]))
                or count < NEWS_PAGE_SIZE
                or total_news is not None and page * NEWS_PAGE_SIZE >= total_news
                or page == MAX_NEWS_PAGE
            ):
                db.execute("DELETE FROM news_gap")
                next_page = None
            else:
                # 주기 사이에 앞쪽 기사가 삭제돼 다음 기사가 한 쪽 앞으로 이동할 수 있다.
                # 다음 주기의 첫 요청은 이번 주기의 마지막 쪽을 겹쳐 읽는다.
                db.execute(
                    "UPDATE news_gap SET page=?,last_ids=?,retry_at=?",
                    (
                        page + 1 if more_this_cycle else page,
                        _json(sorted(source_ids)) if more_this_cycle else "[]",
                        gap["retry_at"] if more_this_cycle else "",
                    ),
                )
                next_page = page + 1 if more_this_cycle else None
        self.export(incremental=True)
        return next_page

    @staticmethod
    def _row(row) -> dict:
        return {
            "revision": row["seq"],
            "stream": row["stream"],
            "received_at": row["received_at"],
            "item": json.loads(row["payload"]),
            "view": json.loads(row["view"]),
        }

    def export(self, *, incremental: bool = False) -> None:
        """새 버전만 추가. 쓰기 중단으로 파일 크기가 다르면 DB에서 해당 파일만 복구한다."""
        with self._connect() as db:
            # 다른 import 프로세스도 같은 DB 잠금을 거쳐 파일·커서를 동시에 갱신한다.
            db.execute("BEGIN IMMEDIATE")
            states = {row["path"]: row for row in db.execute("SELECT * FROM exports")}
            floor = max((row["last_seq"] for row in states.values()), default=0)
            pending = db.execute(
                "SELECT * FROM revisions WHERE seq>? ORDER BY seq", (floor if incremental else 0,)
            ).fetchall()
            groups: dict[str, list] = {}
            for row in pending:
                name = row["received_at"][:10] + "/" + row["stream"].split("_")[0] + ".jsonl"
                groups.setdefault(name, []).append(row)
            for name, state in states.items():
                path = self.root / name
                if not path.exists() or path.stat().st_size != state["byte_count"]:
                    groups.setdefault(name, [])
            for name, pending_rows in groups.items():
                path = self.root / name
                state = states.get(name)
                healthy = (
                    incremental
                    and state
                    and path.exists()
                    and path.stat().st_size == state["byte_count"]
                )
                if healthy:
                    with path.open("a", encoding="utf-8", newline="") as f:
                        for row in pending_rows:
                            f.write(_json(self._row(row)) + "\n")
                    last_seq = max((row["seq"] for row in pending_rows), default=state["last_seq"])
                else:
                    day, filename = name.split("/")
                    kind = filename.removesuffix(".jsonl")
                    rows = db.execute(
                        "SELECT * FROM revisions WHERE substr(received_at,1,10)=? "
                        "AND (stream=? OR stream LIKE ?) ORDER BY seq",
                        (day, kind, kind + "_%"),
                    ).fetchall()
                    atomic_text(path, "".join(_json(self._row(row)) + "\n" for row in rows))
                    last_seq = max((row["seq"] for row in rows), default=0)
                db.execute(
                    "INSERT OR REPLACE INTO exports VALUES (?,?,?)",
                    (name, last_seq, path.stat().st_size),
                )
            # 스트림별 마지막·마지막 성공 캡처: (stream, seq) 인덱스로 뒤에서부터 찾아 첫 일치에서
            # 멈춘다 — 저장마다 보관 기간 전체 payload 를 훑지 않게(Codex 리뷰)
            heads = db.execute("SELECT stream, MAX(seq) FROM captures GROUP BY stream").fetchall()
            latest = [
                db.execute("SELECT payload FROM captures WHERE seq=?", (seq,)).fetchone()
                for _, seq in heads
            ]
            good = [
                row
                for stream, _ in heads
                if (row := db.execute(_LAST_GOOD_SQL, (stream,)).fetchone()) is not None
            ]
            successes = {json.loads(row[0])["stream"]: json.loads(row[0]) for row in good}
            for stream, payload in successes.items():
                atomic_json(self.root / "latest" / f"{stream}.json", payload)
            previous = self._status_or_empty()
            streams = {}
            for row in latest:
                payload = json.loads(row[0])
                stream = payload["stream"]
                streams[stream] = {
                    "view": payload.get("view", {}),
                    "status": payload["status"],
                    "reason": payload["reason"],
                    "last_attempt_at": payload["observed_at"],
                    "last_success_at": successes.get(stream, {}).get("observed_at"),
                    "count": len(payload["items"]),
                    "elapsed_s": payload["elapsed_s"],
                    "target_interval_s": payload["target_interval_s"],
                    "overrun_s": max(0, payload["elapsed_s"] - payload["target_interval_s"]),
                    "last_error": None,
                }
            # 저장 뒤에 기록된 실패 시도(`record_failure`)는 다른 스트림 저장에 지워지지 않게 옮긴다
            # (그 스트림의 새 캡처가 더 최근이면 지운다).
            for stream, old in previous.items():
                if not isinstance(old, dict) or not isinstance(old.get("last_error"), str):
                    continue
                attempt = old.get("last_attempt_at")
                row = streams.get(stream)
                if row is None:
                    streams[stream] = old
                elif isinstance(attempt, str) and attempt > row["last_attempt_at"]:
                    row.update(last_attempt_at=attempt, last_error=old["last_error"])
            atomic_json(self.root / "status.json", {"streams": streams})

    def _status_or_empty(self) -> dict:
        try:
            streams = self.status().get("streams")
        except (OSError, ValueError):
            return {}
        return streams if isinstance(streams, dict) else {}

    def record_failure(self, stream: str, *, at: datetime, error: str) -> None:
        """응답 거절도 상태에 남긴다: `last_attempt_at`·`last_error`(고정 코드)만 바꾸고
        `last_success_at`·`count` 는 그대로. 기록 실패는 응답 경로를 막지 않는다 — 응답 경로라
        잠금은 1초만 기다리고 못 잡으면 기록을 건너뛴다(확장 요청 제한 20초)."""
        try:
            with self._connect(timeout=1) as db:
                db.execute("BEGIN IMMEDIATE")  # save() 의 status.json 재작성과 겹치지 않게
                streams = self._status_or_empty()
                row = streams.get(stream)
                if not isinstance(row, dict):
                    row = {"status": "error", "reason": error, "last_success_at": None,
                           "count": 0}
                stamp = at.astimezone(UTC).isoformat()
                last = row.get("last_attempt_at")
                if not isinstance(last, str) or stamp >= last:
                    row.update(last_attempt_at=stamp, last_error=error)
                streams[stream] = row
                atomic_json(self.root / "status.json", {"streams": streams})
        except Exception:  # noqa: BLE001 — 상태 기록 실패가 거절 응답을 바꾸지 않는다
            return

    def prune(self, *, today: date, keep_days: int) -> dict[str, int]:
        """보관 기간(`keep_days`)보다 오래된 이력을 지운다(멱등): 변경 버전·화면 캡처(스트림별
        마지막 것은 남김 — latest·status 의 원본), 날짜 폴더 JSONL, PDF 원본(추출 텍스트·manifest 는
        작아서 남김). 지운 SQLite 공간은 파일을 줄이지 않고 다음 쓰기가 재사용한다."""
        cutoff = today - timedelta(days=keep_days)
        stamp = datetime.combine(cutoff, time.min, UTC).isoformat()
        out = {"revisions": 0, "captures": 0, "day_dirs": 0, "pdfs": 0}
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            out["revisions"] = db.execute(
                "DELETE FROM revisions WHERE received_at < ?", (stamp,)
            ).rowcount
            db.execute(
                "DELETE FROM legacy_news_ids WHERE NOT EXISTS "
                "(SELECT 1 FROM revisions WHERE stream='news_all' "
                "AND item_id=legacy_news_ids.item_id)"
            )
            # 스트림별 마지막 캡처와 마지막 성공 캡처는 남긴다(latest·status 의 원본)
            keep = {
                seq
                for _, seq in db.execute("SELECT stream, MAX(seq) FROM captures GROUP BY stream")
            }
            keep |= {
                seq
                for (seq,) in db.execute(
                    f"SELECT MAX(seq) FROM captures WHERE {_GOOD} GROUP BY stream"
                )
            }
            out["captures"] = db.execute(
                "DELETE FROM captures WHERE observed_at < ? "
                "AND seq NOT IN (SELECT value FROM json_each(?))",
                (stamp, json.dumps(sorted(keep))),
            ).rowcount
            # 지운 날짜의 내보내기 커서도 지운다(남기면 export 가 파일이 없다고 보고 다시 만든다)
            db.execute("DELETE FROM exports WHERE substr(path, 1, 10) < ?", (cutoff.isoformat(),))
        for d in sorted(self.root.iterdir()):
            try:
                day = date.fromisoformat(d.name)
            except ValueError:
                continue
            if d.is_dir() and day < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                out["day_dirs"] += 1
        attachments = self.root / "attachments"
        if attachments.is_dir():
            limit = datetime.combine(cutoff, time.min, UTC).timestamp()
            for pdf in attachments.glob("*.pdf"):
                try:
                    if pdf.stat().st_mtime < limit:
                        pdf.unlink()
                        out["pdfs"] += 1
                except OSError:
                    continue
        return out

    def status(self) -> dict:
        p = self.root / "status.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"streams": {}}

    def identify_api_news(self, items: list[Item], rows: list[dict]) -> list[Item]:
        """서버 ID로 기사를 식별하고, 같은 발행시각의 구버전 링크만 승계한다."""
        occurrences = Counter((item.id, item.published_at) for item in items)
        resolved = []
        with self._connect() as db:
            for item, row in zip(items, rows, strict=True):
                source = str(row["id"])
                canonical = identity("news-source", source)
                alias = db.execute(
                    "SELECT item_id FROM legacy_news_ids WHERE source_id=?", (source,)
                ).fetchone()
                if alias:
                    canonical = alias[0]
                elif occurrences[item.id, item.published_at] == 1:
                    # 같은 제목의 과거 버전 중 링크를 처음 만든 기사의 발행시각을 비교한다.
                    legacy = db.execute(
                        "SELECT json_extract(payload,'$.published_at') FROM revisions "
                        "WHERE stream='news_all' AND item_id=? ORDER BY seq LIMIT 1",
                        (item.id,),
                    ).fetchone()
                    published = item.published_at.isoformat() if item.published_at else None
                    if legacy and legacy[0] == published:
                        # 이미 서버 ID로 저장한 기사는 이후 번역 제목이 바뀌어도 링크를 유지한다.
                        adopted = db.execute(
                            "INSERT OR IGNORE INTO legacy_news_ids(source_id,item_id) "
                            "SELECT ?,? WHERE NOT EXISTS (SELECT 1 FROM revisions "
                            "WHERE stream='news_all' AND item_id=?)", (source, item.id, canonical),
                        )
                        if adopted.rowcount:
                            canonical = item.id
                resolved.append(item.model_copy(update={"id": canonical}))
        return resolved

    def cached_news_details(self, items: list[Item], *, asof: datetime) -> dict[str, Item]:
        """수신한 기사 ID별로 그 시점까지 저장된 가장 최근 본문을 찾는다."""
        if asof.tzinfo is None:
            raise ValueError("asof에 시간대가 필요합니다")
        pending = [item for item in items if not item.body]
        if not pending:
            return {}
        stamp = asof.astimezone(UTC).isoformat()
        cached = {}
        with self._connect() as db:
            for item in pending:
                published = item.published_at.isoformat() if item.published_at else None
                row = db.execute(
                    _CACHED_NEWS_DETAIL_SQL, (item.id, stamp, stamp, published, published)
                ).fetchone()
                if row:
                    cached[item.id] = Item.model_validate_json(row["payload"])
        return cached

    def reuse_news_details(self, items: list[Item], *, asof: datetime) -> list[Item]:
        """새 목록의 기사에 기존 본문을 채워 상세 조회 실패 시에도 보존한다."""
        cached = self.cached_news_details(items, asof=asof)
        return [
            item.model_copy(
                update={
                    key: getattr(cached[item.id], key)
                    for key in ("body", "detail_status", "published_at")
                }
            )
            if item.id in cached and not item.body
            else item
            for item in items
        ]

    def query(self, *, asof: datetime, ticker: str | None = None, limit: int = 100) -> list[dict]:
        if asof.tzinfo is None:
            raise ValueError("asof에 시간대가 필요합니다")
        stamp = asof.astimezone(UTC).isoformat()
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM revisions WHERE seq IN "
                "(SELECT MAX(seq) FROM revisions WHERE received_at<=? "
                "GROUP BY stream,item_id UNION SELECT MAX(seq) FROM revisions "
                "WHERE received_at<=? AND json_extract(payload,'$.kind')='news' "
                "AND json_extract(payload,'$.detail_status')='ok' GROUP BY item_id) "
                "ORDER BY received_at DESC,seq DESC",
                (stamp, stamp),
            ).fetchall()
            current = db.execute(
                "SELECT stream,payload FROM captures WHERE seq IN "
                "(SELECT MAX(seq) FROM captures WHERE observed_at<=? "
                "AND json_extract(payload,'$.status') IN ('ok','partial') "
                "AND json_array_length(payload,'$.items')>0 GROUP BY stream)",
                (stamp,),
            ).fetchall()
        membership = {
            r["stream"]: {x["id"] for x in json.loads(r["payload"])["items"]} for r in current
        }
        result = []
        seen = {}
        for row in rows:
            item = json.loads(row["payload"])
            if item.get("kind") in ("quote", "event", "trend") and item["id"] not in membership.get(
                row["stream"], set()
            ):
                continue
            if ticker and ticker.upper() not in item.get("tickers", []):
                continue
            if item.get("published_at") and item["published_at"] > stamp:
                continue
            dedup = item["id"] if item.get("kind") in ("news", "post", "comment") else None
            record = {
                "stream": row["stream"],
                "received_at": row["received_at"],
                "item": item,
                "view": json.loads(row["view"]),
            }
            if dedup and dedup in seen:
                index = seen[dedup]
                prior = result[index]["item"]
                if (
                    item.get("kind") == "news"
                    and item.get("detail_status") == "ok"
                    and prior.get("detail_status") != "ok"
                ):
                    result[index] = record
                continue
            if dedup:
                seen[dedup] = len(result)
            result.append(record)
        return sorted(result, key=lambda r: r["received_at"], reverse=True)[:limit]
