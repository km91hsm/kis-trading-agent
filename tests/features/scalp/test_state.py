"""scalp 상태 파일·사건 기록·키."""

import json
from datetime import UTC, date, datetime

from trader.features.scalp import state

D = date(2026, 11, 4)
NOW = datetime(2026, 11, 4, 1, 2, 3, tzinfo=UTC)


def test_state_roundtrip_and_default(tmp_path):
    assert state.load_state(tmp_path, D, "KR") == {
        "open": [],
        "closed": [],
        "done": [],
        "halted": None,
    }
    data = {"open": [{"ticker": "005930", "name": "삼성전자"}], "closed": [], "done": ["000660"]}
    state.save_state(tmp_path, D, "KR", data)
    d = state.state_dir(tmp_path, D)
    assert d == tmp_path / "scalp" / "2026-11-04"
    assert state.load_state(tmp_path, D, "KR") == data
    assert "삼성전자" in (d / "KR.json").read_text(encoding="utf-8")  # ensure_ascii=False
    assert list(d.glob("*.tmp")) == []


def test_event_uses_injected_clock(tmp_path):
    state.event(tmp_path, D, "KR", "entry", now=NOW, ticker="005930")
    state.event(tmp_path, D, "US", "judge", now=NOW, go=None)
    lines = (state.state_dir(tmp_path, D) / "events.jsonl").read_text(encoding="utf-8")
    first = json.loads(lines.splitlines()[0])
    assert first == {"at": NOW.isoformat(), "market": "KR", "kind": "entry", "ticker": "005930"}
    rows = state.read_events(tmp_path, D)
    assert [r["kind"] for r in rows] == ["entry", "judge"] and rows[1]["go"] is None
    assert state.read_events(tmp_path, date(2026, 11, 5)) == []


def test_keys():
    assert state.entry_key("KR", D, "005930") == "entry:KR:2026-11-04:005930"
    pos = {"ticker": "005930", "entry_at": "2026-11-04T01:00:00+00:00", "entry_time": "100000"}
    assert state.advice_key("KR", pos) == "hold:KR:005930:2026-11-04T01:00:00+00:00"
    assert state.advice_key("KR", {"ticker": "005930", "entry_time": "100000"}) == (
        "hold:KR:005930:100000"
    )


def test_queue_note_appends_to_outbox():
    st: dict = {}
    state.queue_note(st, kind="entry", ticker="005930")
    state.queue_note(st, kind="exit", ticker="005930")
    assert [i["kind"] for i in st["outbox"]] == ["entry", "exit"]
