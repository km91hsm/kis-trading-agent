"""데몬 게이트: should_skip 순수 판정, accounts 가 공급하는 market·monitor 게이트, stale 정리.

옛 tests/test_daemon.py 의 should_skip·market_job·monitor_job 테스트를 새 형태로 이식했다
(should_skip 은 키워드 콜러블, 잡은 JobSpec + deps.gates + run_job)."""

import threading
from dataclasses import replace
from datetime import UTC, date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from trader.features.accounts.gates import GATES
from trader.kernel.config import ConfigError, LlmSettings, market_hours
from trader.kernel.config.market_hours import is_us_holiday
from trader.kernel.daemon.gates import should_skip
from trader.kernel.daemon.jobs import build
from trader.kernel.daemon.runner import Daemon, DaemonDeps, run_job
from trader.kernel.ledger import connect, init_schema
from trader.kernel.ledger.control import set_flag
from trader.kernel.registry import discover, hooks
from trader.kernel.registry.contracts import all_migrations
from trader.kernel.registry.types import Feature, Hook, JobSpec

KR_HOLIDAY = date(2026, 10, 9)


def spec(**kw) -> JobSpec:
    base = dict(
        id="j_{market}", kind="kind_a", trigger="interval", per_market=True,
        schedule_key=None, interval_s=60, enabled=None, gate="market", heavy=False,
        misfire="default", catch_up="none", start_soon_s=0, body=lambda ctx, job: {"ran": 1},
    )  # fmt: skip
    base.update(kw)
    return JobSpec(**base)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.t = now

    def __call__(self) -> datetime:
        return self.t


def make_deps(project, now: datetime, *, holidays=frozenset({KR_HOLIDAY})) -> DaemonDeps:
    db = project / "state" / "t.db"

    def factory():
        return connect(db)

    c = factory()
    init_schema(c, all_migrations(discover()))
    c.close()
    calls = []

    def holiday_fn(market, d):
        calls.append((market, d))
        return d in holidays if market == "KR" else is_us_holiday(d)

    window = lambda s, e: SimpleNamespace(monitor=SimpleNamespace(start=s, end=e))  # noqa: E731
    deps = DaemonDeps(
        settings=SimpleNamespace(llm=LlmSettings()),
        schedule=SimpleNamespace(
            kr=window("09:00", "15:30"), us=window("09:30", "16:00"), ledger_backup="17:10",
            retention="17:20",
        ),
        conn_factory=factory, state=project / "state", now=Clock(now), markets=["KR", "US"],
        gates={}, stop_event=threading.Event(), dry_run=False,
        ports={"accounts": SimpleNamespace(holiday_fn=lambda: holiday_fn)},
    )  # fmt: skip
    for g in GATES:
        deps.gates[g.name] = g.build(None, deps)
    return deps


def job_for(deps, market: str, gate: str):
    feature = Feature(id="f", tier="lane", jobs=(spec(gate=gate),))
    jobs = build([feature], schedule=deps.schedule, settings=deps.settings,
                 markets=deps.markets, ctx=deps)  # fmt: skip
    return next(j for j in jobs if j.market == market)


def run(deps, market: str, gate: str) -> dict:
    conn = deps.conn_factory()
    try:
        return run_job(conn, job_for(deps, market, gate), deps, stop_event=deps.stop_event)
    finally:
        conn.close()


def test_should_skip_weekend_holiday_paused():
    holidays = {KR_HOLIDAY}
    off = lambda: False  # noqa: E731
    is_h = lambda d: d in holidays  # noqa: E731
    assert should_skip("KR", date(2026, 10, 10), paused=off, is_holiday=is_h) == "주말"
    assert should_skip("KR", date(2026, 10, 9), paused=off, is_holiday=is_h) == "휴장일"
    assert should_skip("KR", date(2026, 10, 8), paused=off, is_holiday=is_h) is None
    on = lambda: True  # noqa: E731
    assert should_skip("KR", date(2026, 10, 8), paused=on, is_holiday=is_h) == "일시정지"
    assert should_skip("US", date(2026, 10, 8), paused=on, is_holiday=lambda d: False) == "일시정지"


def test_jobs_skip_holiday(project):
    """휴장일에는 잡 본체가 실행되지 않고 사유만 남는다."""
    deps = make_deps(project, datetime(2026, 10, 9, 0, 0, tzinfo=UTC))
    assert run(deps, "KR", "market") == {"skipped": "휴장일"}
    deps.now.t = datetime(2026, 10, 8, 0, 0, tzinfo=UTC)
    assert run(deps, "KR", "market") == {"ran": 1}


def test_paused_flag_skips_job(project):
    deps = make_deps(project, datetime(2026, 10, 8, 0, 0, tzinfo=UTC))
    conn = deps.conn_factory()
    set_flag(conn, "paused", True)
    conn.close()
    assert run(deps, "KR", "market") == {"skipped": "일시정지"}


def test_monitor_job_runs_only_in_window(project):
    deps = make_deps(project, datetime(2026, 10, 8, 3, 0, tzinfo=UTC))  # 12:00 KST
    assert run(deps, "KR", "monitor") == {"ran": 1}
    deps.now.t = datetime(2026, 10, 8, 10, 0, tzinfo=UTC)  # 19:00 KST
    assert run(deps, "KR", "monitor") == {"skipped": "창 밖"}
    deps.now.t = datetime(2026, 10, 8, 16, 0, tzinfo=UTC)  # 12:00 EDT → US 창 안
    assert run(deps, "US", "monitor") == {"ran": 1}
    # interval 잡은 건너뜀·성공 모두 runs 행을 남기지 않는다
    conn = deps.conn_factory()
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    conn.close()


def test_should_skip_uses_market_timezone(project):
    # KST 토요일 03:00 = 미국 금요일 14:00 ET → US 는 주말이 아니다
    deps = make_deps(project, datetime(2026, 10, 10, 3, 0, tzinfo=ZoneInfo("Asia/Seoul")))
    assert run(deps, "US", "market") == {"ran": 1}
    assert run(deps, "KR", "market") == {"skipped": "주말"}
    deps.now.t = datetime(2026, 10, 11, 12, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    assert run(deps, "US", "market") == {"skipped": "주말"}


def test_monitor_window_in_market_timezone(project):
    deps = make_deps(project, datetime(2026, 10, 8, 14, 0, tzinfo=UTC))  # 10:00 EDT
    assert run(deps, "US", "monitor") == {"ran": 1}
    deps.now.t = datetime(2026, 10, 8, 13, 0, tzinfo=UTC)  # 09:00 EDT, 개장 전
    assert run(deps, "US", "monitor") == {"skipped": "창 밖"}


def test_us_holiday_skipped(project):
    """US 휴장일은 정적 NYSE 달력으로 건너뛴다."""
    skip = lambda d: should_skip(  # noqa: E731
        "US", d, paused=lambda: False, is_holiday=is_us_holiday
    )
    assert skip(date(2026, 12, 25)) == "휴장일"
    assert skip(date(2026, 11, 26)) == "휴장일"
    assert skip(date(2026, 7, 3)) == "휴장일"  # 7/4 토요일 대체
    assert skip(date(2027, 6, 18)) == "휴장일"
    assert skip(date(2026, 12, 24)) is None
    assert len(market_hours.us_holidays(2026) | market_hours.us_holidays(2027)) == 20
    kr = should_skip("KR", date(2026, 12, 25), paused=lambda: False, is_holiday=lambda d: False)
    assert kr is None  # KR 휴장은 KIS 조회(holiday_fn) 소관
    deps = make_deps(project, datetime(2026, 12, 25, 15, 0, tzinfo=UTC))
    assert run(deps, "US", "market") == {"skipped": "휴장일"}
    deps.now.t = datetime(2026, 12, 24, 15, 0, tzinfo=UTC)
    assert run(deps, "US", "market") == {"ran": 1}


def test_build_rejects_job_with_unregistered_gate(project):
    deps = make_deps(project, datetime(2026, 10, 8, 0, 0, tzinfo=UTC))
    deps.gates.pop("monitor")
    feature = Feature(id="f", tier="lane", jobs=(spec(gate="monitor"),))
    with pytest.raises(ConfigError, match="monitor"):
        build([feature], schedule=deps.schedule, settings=deps.settings,
              markets=deps.markets, ctx=deps)  # fmt: skip


@pytest.fixture
def paused_scheduler(monkeypatch):
    from apscheduler.schedulers.background import BackgroundScheduler

    # 기동 정리·잠금만 검증한다. 과거 fixture의 예약 잡이 원장 상태를 바꾸지 않게 한다.
    real_start = BackgroundScheduler.start
    monkeypatch.setattr(BackgroundScheduler, "start", lambda self: real_start(self, paused=True))


def test_run_closes_stale_running_calls(project, paused_scheduler):
    now = datetime(2026, 10, 10, 1, 0, tzinfo=UTC)
    deps = make_deps(project, now)
    conn = deps.conn_factory()
    for created in ("2026-10-09T20:00:00+00:00", "2026-10-10T00:50:00+00:00"):
        conn.execute(
            "INSERT INTO agent_calls(agent, status, created_at) "
            "VALUES ('orchestrator','running',?)",
            (created,),
        )
    conn.commit()
    conn.close()
    seen = []
    holder = []
    feature = Feature(
        id="f", tier="lane",
        hooks=(Hook("daemon.bootstrap", lambda deps: seen.append((deps, holder[0]._scheduler))),),
    )  # fmt: skip
    d = Daemon([feature], replace(deps, hooks=hooks.by_point([feature])))
    holder.append(d)
    try:
        assert d.start(install_signals=False) is True
    finally:
        d.shutdown(wait=True)
    conn = deps.conn_factory()
    rows = conn.execute("SELECT status, error FROM agent_calls ORDER BY id").fetchall()
    conn.close()
    assert [r["status"] for r in rows] == ["error", "running"]
    assert "재기동" in rows[0]["error"]
    assert seen == [(d.deps, None)]  # deps= 로 호출, 잡 등록(스케줄러 생성) 전



def test_start_without_lock_runs_no_bootstrap_or_stale_cleanup(project, paused_scheduler):
    deps = make_deps(project, datetime(2026, 10, 10, 1, 0, tzinfo=UTC))
    seen = []
    feature = Feature(
        id="f", tier="lane", hooks=(Hook("daemon.bootstrap", lambda deps: seen.append(1)),)
    )
    first = Daemon([Feature(id="g", tier="lane")], deps)
    second = Daemon([feature], replace(deps, hooks=hooks.by_point([feature])))
    try:
        assert first.start(install_signals=False) is True
        conn = deps.conn_factory()
        conn.execute(
            "INSERT INTO agent_calls(agent, status, created_at) "
            "VALUES ('orchestrator','running','2026-10-09T20:00:00+00:00')"
        )
        conn.commit()
        conn.close()
        assert second.start(install_signals=False) is False
    finally:
        second.shutdown(wait=True)
        first.shutdown(wait=True)
    conn = deps.conn_factory()
    (status,) = conn.execute("SELECT status FROM agent_calls").fetchone()
    conn.close()
    assert seen == [] and status == "running"


def test_monitor_gate_ignores_paused_but_market_gate_skips(project):
    """Ruling 20(a): paused 여도 monitor 게이트는 창만 본다 — 감시는 계속 돈다 — 손절선 도달 시
    손절은 실행(`stop_while_paused` 알림)·트리거는 기록하고 재평가만 `monitor_once` 가 막는다.
    주문 경로(market 게이트)는 멈춘다."""
    deps = make_deps(project, datetime(2026, 10, 8, 3, 0, tzinfo=UTC))  # 12:00 KST
    conn = deps.conn_factory()
    set_flag(conn, "paused", True)
    conn.close()
    assert run(deps, "KR", "market") == {"skipped": "일시정지"}
    assert run(deps, "KR", "monitor") == {"ran": 1}
    deps.now.t = datetime(2026, 10, 8, 10, 0, tzinfo=UTC)  # 19:00 KST
    assert run(deps, "KR", "monitor") == {"skipped": "창 밖"}
    deps.now.t = datetime(2026, 10, 9, 3, 0, tzinfo=UTC)  # 휴장일
    assert run(deps, "KR", "monitor") == {"skipped": "휴장일"}


def test_monitor_gate_closes_at_us_early_close(project):
    """US 조기 마감일(추수감사절 다음 날 13:00 ET)에는 감시 창도 13:00 에 끝난다 — schedule 의
    16:00 까지 돌면 장 밖 손절 주문이 거부되며 반복된다."""
    deps = make_deps(project, datetime(2026, 11, 27, 17, 0, tzinfo=UTC))  # 12:00 ET
    assert run(deps, "US", "monitor") == {"ran": 1}
    deps.now.t = datetime(2026, 11, 27, 19, 0, tzinfo=UTC)  # 14:00 ET — 조기 마감 뒤
    assert run(deps, "US", "monitor") == {"skipped": "창 밖"}
    deps.now.t = datetime(2026, 11, 30, 19, 0, tzinfo=UTC)  # 평일 14:00 ET 는 창 안
    assert run(deps, "US", "monitor") == {"ran": 1}


def test_monitor_gate_keeps_user_window_on_regular_days(project):
    """창을 당기는 것은 US 조기 마감일뿐 — 평소에는 schedule 의 창 끝을 그대로 쓴다(KR 수능일처럼
    마감이 늦는 날 사용자가 창을 늘릴 수 있게)."""
    deps = make_deps(project, datetime(2026, 10, 8, 6, 45, tzinfo=UTC))  # 15:45 KST
    deps.schedule.kr.monitor.end = "16:30"
    assert run(deps, "KR", "monitor") == {"ran": 1}
