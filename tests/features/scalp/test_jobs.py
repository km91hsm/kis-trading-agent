"""데몬 ↔ 단타: 잡 등록·꺼져 있으면 skipped·창 밖 skipped·켜면 engine 틱·건너뛸 때 outbox flush·
판정자 teardown.

옛 `tests/test_daemon_scalp.py` 3건 이식 — 치환만: `daemon.build_jobs(SCHED, …)`→
`kernel.daemon.jobs.build([FEATURE, accounts FEATURE], …)`, `_deps`→`DaemonDeps`(monitor 게이트는
accounts 공급자로 만든다), `daemon._scalp_body`→`jobs.scalp_body`, `daemon.job_body(deps, job)()`→
`runner.run_job`(게이트 none → 본문이 창·휴장을 거른다), `engine.save_state(None, …)`→
`state.save_state(project/state, …)`. 신규: 판정자 teardown(Review Focus 5)·일시정지."""

import threading
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from tests.features.scalp.conftest import NOW, Notes
from trader.features.accounts import modes
from trader.features.accounts.feature import FEATURE as ACCOUNTS
from trader.features.accounts.gates import build_monitor_gate
from trader.features.scalp import jobs as scalp_jobs
from trader.features.scalp import judge, switch
from trader.features.scalp.feature import FEATURE
from trader.features.scalp.schemas import ScalpVerdict
from trader.features.scalp.state import load_state, save_state
from trader.kernel.cli.boot import build_registry
from trader.kernel.config.loader import load
from trader.kernel.daemon import jobs as daemon_jobs
from trader.kernel.daemon import runner
from trader.kernel.ledger import db
from trader.kernel.ledger.control import delete_flag, set_flag
from trader.kernel.registry import discover
from trader.kernel.session.run_store import run_date_for


@pytest.fixture
def schedule(project):
    return load("schedule", build_registry(discover()), path=project / "config" / "x.yaml")


@pytest.fixture
def make_deps(settings, schedule, ports, project, conn):
    """`DaemonDeps`(monitor 게이트는 accounts 공급자, 휴장 없음). conn 픽스처가 스키마를 만든다."""
    ports["accounts"].holiday_fn = lambda: (lambda m, d: False)

    def make(now=NOW, **kw) -> runner.DaemonDeps:
        base = {
            "settings": settings,
            "schedule": schedule,
            "conn_factory": db.connect,
            "state": project / "state",
            "now": lambda: now,
            "markets": ["KR", "US"],
            "gates": {},
            "stop_event": threading.Event(),
            "dry_run": False,
            "notifier": Notes(),
            "ports": ports,
        }
        deps = runner.DaemonDeps(**(base | kw))
        deps.gates["monitor"] = build_monitor_gate(None, deps)
        return deps

    return make


def _job(deps, market="KR"):
    built = daemon_jobs.build(
        [FEATURE, ACCOUNTS], schedule=deps.schedule, settings=deps.settings,
        markets=[market], ctx=deps,
    )  # fmt: skip
    return next(j for j in built if j.id == f"{market.lower()}_scalp")


def test_build_jobs_registers_scalp_interval_per_market(settings, schedule):
    ctx = SimpleNamespace(gates={"market": None, "monitor": None})
    jobs = {
        j.id: j
        for j in daemon_jobs.build(
            [FEATURE, ACCOUNTS], schedule=schedule, settings=settings, markets=["KR", "US"], ctx=ctx
        )
    }
    assert jobs["kr_scalp"].kind == "scalp" and jobs["kr_scalp"].interval_s == 60
    assert jobs["us_scalp"].market == "US"
    assert jobs["kr_scalp"].spec.gate == "none"
    assert jobs["kr_scalp"].spec.teardown is scalp_jobs.judge_teardown


def test_scalp_body_skipped_when_disabled_or_outside_window(make_deps, settings, conn, project):
    cfg = settings.scalp.model_copy(update={"enabled": False})
    off = settings.model_copy(update={"scalp": cfg})
    deps = make_deps(settings=off)
    assert scalp_jobs.scalp_body(deps, _job(deps)) == {"skipped": "단타 켜진 계좌 없음"}
    set_flag(conn, switch.flag_key("paper-main"), True)  # 계좌 스위치가 yaml 보다 앞선다
    assert "skipped" not in scalp_jobs.scalp_body(deps, _job(deps))
    delete_flag(conn, switch.flag_key("paper-main"))
    deps = make_deps()  # NOW = 수 10:30 KST, 켜짐
    out = scalp_jobs.scalp_body(deps, _job(deps))
    assert out["entries"] == [] and out["candidates"] == 0  # _scan 없음 → 후보 0
    night = datetime(2026, 11, 4, 14, 0, tzinfo=UTC)  # 23:00 KST
    deps = make_deps(now=night)
    assert "skipped" in scalp_jobs.scalp_job(deps, _job(deps))
    assert scalp_jobs.scalp_body(deps, _job(deps)) == {"skipped": "창 밖"}
    deps = make_deps()
    modes.set_paused(conn, True)
    out = scalp_jobs.scalp_body(deps, _job(deps))  # 일시정지여도 틱은 돈다(skipped 는 틱 안의 표시)
    assert out["skipped"] == "일시정지" and "entries" in out
    cfg = settings.scalp.model_copy(update={"enabled": False})
    off = settings.model_copy(update={"scalp": cfg})
    deps = make_deps(settings=off)  # 스위치도 상태도 없으면 틱을 돌리지 않는다
    assert scalp_jobs.scalp_body(deps, _job(deps)) == {"skipped": "단타 켜진 계좌 없음"}
    day = run_date_for("KR", NOW)
    save_state(project / "state", day, "KR", {"open": [{"ticker": "005930", "status": "closing"}]})
    out = scalp_jobs.scalp_body(deps, _job(deps))  # 열린 다리가 있으면 돈다
    assert out["skipped"] == "일시정지" and "entries" in out


def test_scalp_body_skips_when_monitor_gate_missing(make_deps):
    """monitor 게이트가 없으면 창·휴장을 거를 수 없다 — 통과가 아니라 fail-closed."""
    deps = make_deps()
    deps.gates.pop("monitor")
    assert scalp_jobs.scalp_body(deps, _job(deps)) == {"skipped": "monitor 게이트 없음"}


def test_scalp_job_flushes_queued_digest_when_tick_is_skipped(make_deps, conn, project):
    """Codex 리뷰(T3): 묶음 알림이 outbox 에 남은 채 단타를 끄거나 창 밖·일시정지면 tick 이 돌지
    않아 영영 안 간다 → 잡이 건너뛸 때 그날 묶음만 보낸다."""
    now = datetime(2026, 11, 4, 14, 0, tzinfo=UTC)  # 수 23:00 KST — 창 밖
    sent = Notes()
    deps = make_deps(now=now, notifier=sent)
    state = project / "state"
    day = run_date_for("KR", now)
    item = {"leg": "exit", "label": "005930", "price": "10300", "reason": "close", "ret": 0.01}
    save_state(state, day, "KR", {"open": [], "outbox": [item]})
    job = _job(deps)
    stop = threading.Event()
    assert "skipped" in runner.run_job(conn, job, deps, stop_event=stop)
    assert len(sent.msgs) == 1 and "청산 005930 @10,300원 · 마감 청산" in sent.msgs[0]
    assert load_state(state, day, "KR")["outbox"] == []
    runner.run_job(conn, job, deps, stop_event=stop)
    assert len(sent.msgs) == 1  # 비운 뒤엔 다시 안 보낸다


def test_judge_teardown_shuts_pool_and_stop_event_short_circuits(make_deps, monkeypatch):
    """Review Focus 5: 종료 요청(stop_event)이면 대기열의 판정은 claude 를 띄우지 않고 None,
    잡 teardown 은 전역 판정자 풀을 닫고 비운다."""
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    judge.shutdown_judge()
    deps = make_deps()
    j = judge.judge_for(deps)
    assert judge.judge_for(deps) is j  # 데몬 수명 동안 하나
    deps.stop_event.set()
    j.submit("k", {"kind": "entry"}, ScalpVerdict)
    j._futs["k"].result(timeout=5)
    assert j.poll("k") is None
    scalp_jobs.judge_teardown(deps)
    assert judge._JUDGE is None
    assert j._pool._shutdown is True


def test_scalp_body_exits_open_leg_after_all_switches_off(
    make_deps, conn, project, market, brokers
):
    """최종 검토 C1: 스위치를 모두 꺼도 열린 단타 다리의 손절은 잡에서 나간다(틱은 돈다)."""
    from datetime import timedelta
    from decimal import Decimal

    from tests.features.scalp.conftest import bars, fill_all, scan_checkpoint

    scan_checkpoint(project, "KR", ["005930"])
    market.minutes["005930"] = bars(burst=True)
    market.prices["005930"] = Decimal("10050")
    deps = make_deps()
    out = scalp_jobs.scalp_body(deps, _job(deps))
    assert [e["ticker"] for e in out["entries"]] == ["005930"]
    fill_all(conn, brokers, market)
    for acc in ("paper-main", "paper-two", "real-1"):
        set_flag(conn, switch.flag_key(acc), False)
    assert not switch.any_enabled(conn, deps.settings, deps.ports["accounts"], "KR")
    market.prices["005930"] = Decimal("9800")  # 손절 −1% 아래
    deps = make_deps(now=NOW + timedelta(minutes=3))
    out = scalp_jobs.scalp_body(deps, _job(deps))
    assert out.get("skipped") == "켜진 계좌 없음"  # 신규 진입만 막힌다
    sells = conn.execute("SELECT account_id FROM orders WHERE side='SELL'").fetchall()
    assert {r["account_id"] for r in sells} == {"paper-main", "paper-two"}


def test_scalp_body_skips_without_switches_or_state(make_deps, settings, conn, project):
    """스위치가 모두 꺼졌고 관리할 단타 상태(오늘·이월 창 안의 열린 다리·판정 대기)도 없으면 틱을
    돌리지 않는다. 옛 운영일에 열린 다리가 남아 있으면 이월하러 틱을 돌린다."""
    from datetime import timedelta

    from trader.features.scalp import engine

    cfg = settings.scalp.model_copy(update={"enabled": False})
    deps = make_deps(settings=settings.model_copy(update={"scalp": cfg}))
    state = project / "state"
    day = run_date_for("KR", NOW)
    save_state(state, day, "KR", {"open": [], "closed": [], "done": ["000660"], "pending": {}})
    assert scalp_jobs.scalp_body(deps, _job(deps)) == {"skipped": "단타 켜진 계좌 없음"}
    old = day - timedelta(days=engine.CARRY_DAYS + 1)  # 이월 창 밖
    save_state(state, old, "KR", {"open": [{"ticker": "005930", "entry_at": "x"}]})
    assert scalp_jobs.scalp_body(deps, _job(deps)) == {"skipped": "단타 켜진 계좌 없음"}
    save_state(state, day, "KR", {"open": [], "pending": {"000660": {}}})
    assert scalp_jobs.has_scalp_state(state, "KR", day)
    save_state(state, day, "KR", {"open": []})
    prev = day - timedelta(days=engine.CARRY_DAYS)  # 이월 창 끝
    save_state(state, prev, "KR", {"open": [{"ticker": "005930", "entry_at": "x"}]})
    assert scalp_jobs.has_scalp_state(state, "KR", day)


def test_has_scalp_state_fails_safe_on_corrupt_file(make_deps, settings, project, monkeypatch):
    """상태 파일을 못 읽으면(깨진 JSON) 열린 다리가 있을 수 있다고 보고 틱을 돌린다."""
    from datetime import timedelta

    from trader.features.scalp.state import state_dir

    cfg = settings.scalp.model_copy(update={"enabled": False})
    deps = make_deps(settings=settings.model_copy(update={"scalp": cfg}))
    state = project / "state"
    day = run_date_for("KR", NOW)
    d = state_dir(state, day - timedelta(days=1))
    d.mkdir(parents=True)
    (d / "KR.json").write_text("{깨짐", encoding="utf-8")
    assert scalp_jobs.has_scalp_state(state, "KR", day) is True
    monkeypatch.setattr(scalp_jobs.engine, "tick", lambda *a, **k: {"ran": True})
    assert scalp_jobs.scalp_body(deps, _job(deps)) == {"ran": True}  # 건너뛰지 않고 틱으로
