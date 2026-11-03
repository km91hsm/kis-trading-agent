"""복기 세션: 대기 목록·FAKE 세션·세션 생략·`submit-reflection` 도구·에이전트 정의."""

from datetime import UTC, date, datetime

import pytest

from tests.features.evaluation.helpers import seed_matured
from trader.features.evaluation import reflect, tools
from trader.features.evaluation.memory import store
from trader.features.evaluation.session_kinds import AGENTS, REFLECT, agents_for_runner
from trader.kernel.lane.types import LaneContext
from trader.kernel.session import agents
from trader.kernel.session.agents import ORCHESTRATOR

NOW = datetime(2026, 10, 17, tzinfo=UTC)
D = date(2026, 1, 5)


def _ctx(conn, settings, project):
    return LaneContext(
        settings=settings, conn=conn, state=project / "state", now=lambda: NOW, dry_run=True,
        notifier=None, ports={},
    )  # fmt: skip


def _matured(conn):
    (eid,) = seed_matured(conn, 1, start=D, now=NOW)
    conn.commit()
    return eid


def test_reflector_agent_definition():
    a = agents.load_agent(AGENTS[0])
    assert a.model in ("haiku", "sonnet", "opus") and "lesson" in a.prompt and "한국어" in a.prompt
    assert AGENTS[1].name == "reflect-orchestrator"
    mapping = agents_for_runner()
    assert mapping[ORCHESTRATOR].path == AGENTS[1].path and ORCHESTRATOR not in {
        d.name for d in AGENTS
    }
    assert REFLECT.agents == ("reflector",) and REFLECT.runs_kind == "reflect"


def test_pending_reflections_lists_matured_without_reflection(conn):
    eid = _matured(conn)
    items = reflect.pending(conn, market="KR")
    assert len(items) == 1 and items[0]["entry_id"] == eid
    assert items[0]["analyst_signals"]["technical"] == "BUY"
    assert items[0]["analyst_signals"]["sentiment"] == "SELL"
    assert items[0]["direction_score"] == 0.03
    store.set_reflection(conn, eid, {"decision_review": {"verdict": "GOOD"}})
    assert reflect.pending(conn, market="KR") == []


def test_fake_reflect_session_sets_reflections(conn, settings, project, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    eid = _matured(conn)
    res = reflect.run_reflect(_ctx(conn, settings, project), market="KR", date=D)
    assert res.ok and res.fake and "_reflect" in res.stages_written
    e = store.get_entry(conn, eid)
    assert e["reflection"]["decision_review"]["verdict"] == "NEUTRAL" and e["tier"] == "short"
    assert (
        conn.execute("SELECT kind FROM runs ORDER BY id DESC LIMIT 1").fetchone()["kind"]
        == "reflect"
    )


def test_run_reflect_without_pending_skips_session(conn, settings, project):
    res = reflect.run_reflect(_ctx(conn, settings, project), market="KR", date=D)
    assert res.ok and res.stages_written == [] and res.session_id is None
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_submit_reflection_saves_lesson_without_single_promotion(conn, settings, project):
    """L13: lesson 한 건으로는 승격하지 않는다(같은 상황 결정 3건 이상일 때만, memory/store)."""
    eid = _matured(conn)
    ctx = _ctx(conn, settings, project)
    payload = [
        {
            "entry_id": eid,
            "signal_review": {"technical": {"verdict": "HIT", "note": ""}},
            "decision_review": {"verdict": "GOOD", "note": "좋음"},
            "lesson": "추세 추종 유효",
            "confidence": 0.8,
        }
    ]
    r = tools.submit_reflection(ctx, market="KR", date=D, payload=payload)
    assert r == {"ok": True, "saved": 1, "entry_ids": [eid]}
    e = store.get_entry(conn, eid)
    assert e["tier"] == "short" and e["reflection"]["lesson"] == "추세 추종 유효"
    bad = tools.submit_reflection(
        ctx, market="KR", date=D,
        payload=[{
            "entry_id": 999, "signal_review": {},
            "decision_review": {"verdict": "GOOD", "note": ""}, "confidence": 0.5,
        }],
    )  # fmt: skip
    assert bad == {"ok": False, "error": "메모리 엔트리 없음: 999"}
    assert not tools.submit_reflection(ctx, market="JP", date=D, payload=payload)["ok"]


@pytest.mark.parametrize("bad", [{"entry_id": 1}])
def test_submit_reflection_schema_error_message(conn, settings, project, bad):
    r = tools.submit_reflection(_ctx(conn, settings, project), market="KR", date=D, payload=[bad])
    assert r["ok"] is False and r["error"].startswith("스키마 검증 실패(Reflection[0]):")
