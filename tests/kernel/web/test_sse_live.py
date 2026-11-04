"""실제 기능 전체(`discover()`)로 조립한 SSE 스냅샷: 생성 파일 키 목록과 같고, 화면이 읽는 값이
바뀌면 그 키의 지문이 바뀌며, 같은 값 다시 쓰기·내부 커서·같은 분 하트비트는 바꾸지 않는다.

옛 `tests/web/test_control_sse.py::test_sse_snapshot_covers_live_screens`·
`test_sse_fingerprint_ignores_same_value_rewrites` 를 새 키 목록(커널 + 모든 기능)으로 이식."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

from tests.features.conftest import load_settings
from tests.kernel.web.conftest import make_deps
from trader.kernel.ledger import db
from trader.kernel.ledger.control import set_flag
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations
from trader.kernel.web import gen, sse

NOW = datetime(2026, 10, 26, 3, 0, tzinfo=UTC)


def _deps(project):
    ledger = project / "state" / "trader.db"
    conn = db.connect(ledger)
    db.init_schema(conn, all_migrations(discover()))
    conn.close()
    return make_deps(
        project, settings=load_settings(project), conn_factory=lambda: db.connect(ledger),
        now=lambda: NOW, features=discover(),
    )  # fmt: skip


def _keys_ts() -> list[str]:
    line = next(x for x in gen.sse_keys_ts(discover()).splitlines() if "SSE_KEYS =" in x)
    return json.loads(line.split("=", 1)[1].replace(" as const;", "").strip())


def test_sse_snapshot_covers_live_screens(project):
    deps = _deps(project)
    s0 = sse.snapshot(deps)
    assert list(s0) == _keys_ts()  # 생성 파일 키 목록 = 스냅샷 키(순서 포함)
    for k in ("daemon", "usage", "control", "jobs", "runs", "notices", "news", "theme",
              "watchlist", "positions", "decisions", "orders", "triggers"):  # fmt: skip
        assert k in s0, k
    conn = deps.conn_factory()
    try:
        conn.execute(
            "INSERT INTO llm_rate_limits(observed_at, source, raw_json) VALUES ('x','probe','{}')"
        )
        conn.commit()
        s1 = sse.snapshot(deps)
        assert s1["usage"] != s0["usage"]
        set_flag(conn, "regime:KR", {"state": "bear"})
        s2 = sse.snapshot(deps)
        assert s2["control"] != s1["control"]
        set_flag(conn, "telegram_offset", 99)  # 폴링마다 바뀌는 키는 지문에서 뺀다
        assert sse.snapshot(deps)["control"] == s2["control"]
        conn.execute(
            "INSERT INTO news_items(market, source, title, url, url_hash, received_at) "
            "VALUES ('KR', 'google_rss', '합성', 'https://example.test/1', 'h1', 'x')"
        )
        conn.execute(
            "INSERT INTO theme_heat(market, date, observed_at, theme, rank, score, catalyst_ok, "
            "tickers_json, components_json) VALUES ('KR', 'd', 'x', '합성', 1, 1.0, 1, '[]', '{}')"
        )
        conn.execute(
            "INSERT INTO job_requests(kind, params, status, requested_at) "
            "VALUES ('x', '{}', 'queued', 'x')"
        )
        conn.execute(
            "INSERT INTO runs(kind, market, started_at, status) VALUES ('a', 'KR', 'x', 'running')"
        )
        conn.execute(
            "INSERT INTO notices(at, kind, text, channel, ok) VALUES ('x', 'k', '합성', 't', 1)"
        )
        conn.commit()
        set_flag(conn, "watchlist", ["AAPL"])
        s3 = sse.snapshot(deps)
        for k in ("news", "theme", "jobs", "runs", "notices", "watchlist"):
            assert s3[k] != s2[k], k
    finally:
        conn.close()


def test_sse_fingerprint_ignores_same_value_rewrites(project):
    deps = _deps(project)
    conn = deps.conn_factory()
    try:
        set_flag(conn, "regime:KR", {"state": "bear"})
        set_flag(conn, "watchlist", ["005930"])
        hb = deps.state / "daemon_heartbeat.json"
        hb.write_text(
            json.dumps({"pid": 1, "updated_at": "2026-10-26T03:00:05+00:00"}), encoding="utf-8"
        )
        s0 = sse.snapshot(deps)
        set_flag(conn, "regime:KR", {"state": "bear"})  # 같은 값
        set_flag(conn, "watchlist", ["005930"])  # 같은 값
        set_flag(conn, "news_trigger_at:KR", NOW.isoformat())  # 내부 커서
        hb.write_text(  # 같은 분 안의 대기열 틱
            json.dumps({"pid": 1, "updated_at": "2026-10-26T03:00:35+00:00"}), encoding="utf-8"
        )
        os.utime(hb)
        s1 = sse.snapshot(deps)
        for k in ("control", "watchlist", "daemon"):
            assert s1[k] == s0[k], k
        hb.write_text(
            json.dumps({"pid": 1, "updated_at": "2026-10-26T03:01:05+00:00"}), encoding="utf-8"
        )
        assert sse.snapshot(deps)["daemon"] != s0["daemon"]
    finally:
        conn.close()
