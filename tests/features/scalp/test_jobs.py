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
    ports["accounts"].holiday_fn = lambda: lambda m, d: False

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


def test_scalp_body_skipped_when_disabled_or_outside_window(make_deps, settings, conn):
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
    assert scalp_jobs.scalp_body(deps, _job(deps)) == {"skipped": "일시정지"}


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
