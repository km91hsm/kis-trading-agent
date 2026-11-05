"""kernel/ops 데몬 잡: `ops_check` 등록·본체(점검→보관→격상 알림)·스케줄 잡 펼치기."""

from types import SimpleNamespace

import pytest

from tests.kernel.daemon.test_runner import make_deps
from trader.kernel.config import LlmSettings
from trader.kernel.daemon.builtin_jobs import BUILTIN_JOBS
from trader.kernel.ops import jobs as ops_jobs
from trader.kernel.ops import latest
from trader.kernel.ops.settings import OpsSettings
from trader.kernel.registry.types import Feature, JobSpec


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, text: str, *, escape: bool = True) -> bool:
        self.sent.append(text)
        return True


@pytest.fixture
def daemon_deps(project):
    settings = SimpleNamespace(
        notify=SimpleNamespace(enabled=False),
        llm=LlmSettings(usage_file=str(project / "state" / "rate_limits.json")),
        markets=["KR"],
        ops=OpsSettings(),
    )
    return make_deps(project, settings=settings, notifier=FakeNotifier())


def _job(id_, *, trigger="interval", schedule_key=None, gate="none", interval_s=60):
    return JobSpec(
        id=id_, kind=id_, trigger=trigger, per_market=False, schedule_key=schedule_key,
        interval_s=interval_s, enabled=None, gate=gate, heavy=False, misfire="default",
        catch_up="none", start_soon_s=0, body=lambda deps, job=None: {},
    )  # fmt: skip


def test_ops_check_registered_in_builtin_jobs():
    by_id = {j.id: j for j in BUILTIN_JOBS}
    spec = by_id["ops_check"]
    assert spec is ops_jobs.OPS_CHECK
    assert spec.schedule_key == "ops_check.interval_s" and spec.trigger == "interval"
    assert spec.interval_s == 1800 and spec.gate == "none" and spec.per_market is False
    assert spec.body is ops_jobs.ops_check_body


def test_retention_job_registered_and_body_uses_settings(daemon_deps, monkeypatch):
    by_id = {j.id: j for j in BUILTIN_JOBS}
    spec = by_id["retention"]
    assert spec is ops_jobs.RETENTION and spec.body is ops_jobs.retention_body
    assert spec.schedule_key == "retention" and spec.trigger == "cron"
    assert spec.misfire == "idempotent" and spec.catch_up == "direct" and spec.per_market is False
    seen = {}
    real = ops_jobs.retention.prune

    def spy(conn, state, *, today, keep_days):
        seen.update(state=state, today=today, keep_days=keep_days)
        return real(conn, state, today=today, keep_days=keep_days)

    monkeypatch.setattr(ops_jobs.retention, "prune", spy)
    daemon_deps.settings.ops = OpsSettings(retention_days=45)
    out = ops_jobs.retention_body(daemon_deps)
    assert set(out) == {
        "run_dirs", "control", "job_requests", "runs", "llm_rate_limits", "notices", "control_log",
    }  # fmt: skip
    assert seen["keep_days"] == 45 and seen["state"] == daemon_deps.state
    assert seen["today"].isoformat() == "2026-11-02"  # NOW 01:00 UTC = 10:00 KST


def test_ops_check_body_records_result_and_notifies_once(daemon_deps):
    out = ops_jobs.ops_check_body(daemon_deps)  # 데몬 잠금 없음 → jobs.daemon critical
    assert out["level"] == "critical" and out["notified"] is True and out["items"] >= 1
    assert len(daemon_deps.notifier.sent) == 1 and "점검 격상" in daemon_deps.notifier.sent[0]
    conn = daemon_deps.conn_factory()
    try:
        saved = latest(conn)
    finally:
        conn.close()
    assert saved is not None and saved["level"] == "critical"
    assert any(i["key"] == "jobs.daemon" for i in saved["items"])
    again = ops_jobs.ops_check_body(daemon_deps)  # 같은 결과 → 알림 없음
    assert again["level"] == "critical" and again["notified"] is False
    assert len(daemon_deps.notifier.sent) == 1


def test_ops_check_body_without_notifier(project):
    deps = make_deps(project, settings=SimpleNamespace(llm=LlmSettings(), markets=["KR"]))
    out = ops_jobs.ops_check_body(deps)
    assert out["level"] == "critical" and out["notified"] is False


def test_schedule_jobs_returns_empty_on_config_error(settings):
    cron = _job("bad", trigger="cron", schedule_key="nope")
    bad = Feature(id="bad", tier="service", jobs=(cron,))
    sched = SimpleNamespace(ledger_backup="17:10")
    assert ops_jobs.schedule_jobs([bad], settings=settings, schedule=sched, markets=["KR"]) == []


def test_schedule_jobs_includes_kernel_and_gated_feature_jobs(settings):
    """게이트 이름은 데몬 부트만 안다 — 점검용 펼치기는 게이트 검사를 건너뛴다."""
    gated = Feature(id="g", tier="service", jobs=(_job("g_tick", gate="market"),))
    sched = SimpleNamespace(
        ledger_backup="17:10", retention="17:20", ops_check=SimpleNamespace(interval_s=600)
    )
    got = {j.id: j for j in ops_jobs.schedule_jobs(
        [gated], settings=settings, schedule=sched, markets=["KR"]
    )}  # fmt: skip
    assert {"g_tick", "ops_check", "telegram_poll", "ledger_backup", "retention"} <= set(got)
    assert got["ops_check"].interval_s == 600


def test_build_context_without_schedule_has_no_jobs(conn, settings, project):
    ctx = ops_jobs.build_context(
        conn=conn, settings=settings, state=project / "state", now=None, ports={},
        features=[], schedule=None, markets=["KR"],
    )  # fmt: skip
    assert list(ctx.jobs) == [] and ctx.conn is conn
