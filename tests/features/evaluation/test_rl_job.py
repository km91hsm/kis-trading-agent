"""rl_train 잡·큐 kind·status RL 줄. 옛 test_daemon_queue·test_daemon·test_status 3건 이식."""

from datetime import UTC, datetime
from types import SimpleNamespace

from tests.features.evaluation.test_reflect_job import Sent
from tests.kernel.daemon.test_runner import make_deps
from trader.features.evaluation import jobs, status
from trader.features.evaluation.feature import FEATURE
from trader.features.evaluation.rl import train as rl_train_mod
from trader.kernel.cli.boot import build_registry
from trader.kernel.config.loader import load
from trader.kernel.daemon import queue as jobq
from trader.kernel.daemon import queue_jobs as qj
from trader.kernel.daemon.jobs import build
from trader.kernel.daemon.queue_jobs import QueueState
from trader.kernel.notify import commands
from trader.kernel.registry import discover

NOW = datetime(2026, 11, 7, 1, 30, tzinfo=UTC)  # 토 10:30 KST


def _deps(project, settings):
    return make_deps(
        project, settings=settings, now=lambda: NOW, notifier=Sent(),
        queue=QueueState(kinds=jobq.kinds_of([FEATURE])),
    )  # fmt: skip


def test_rl_train_request_passes_market_and_seeds(project, settings, monkeypatch):
    seen = {}

    def fake(cfg, c, **kw):
        seen["seeds"] = cfg.models.rl_sizer.seeds
        seen["market"] = kw.get("market")
        return {"status": "trained", "version": "v", "summary": {}}

    monkeypatch.setattr(rl_train_mod, "train_sizer", fake)
    deps = _deps(project, settings)
    out = jobs.rl_train_request(deps, {"market": "US", "seeds": 2}, lambda s: None)
    assert seen == {"seeds": 2, "market": "US"} and out["status"] == "trained"
    assert jobs.RlTrainParams(seeds=10).seeds == 10
    for bad in (0, 11):
        try:
            jobs.RlTrainParams(seeds=bad)
        except ValueError:
            continue
        raise AssertionError(bad)
    assert "rl_train" in jobq.kinds_of([FEATURE])


def test_rl_train_job_trains_sizer_even_when_not_enabled(project, settings, monkeypatch):
    calls = []
    monkeypatch.setattr(
        rl_train_mod,
        "train_sizer",
        lambda *a, **k: calls.append(1) or {"status": "trained", "version": "v", "summary": {}},
    )
    (project / "config" / "ml.yaml").write_text(
        "models:\n  rl_sizer: {enabled: false}\n", encoding="utf-8"
    )
    deps = _deps(project, settings)
    r = jobs.rl_train_body(deps)
    assert calls == [1] and r["status"] == "trained"
    assert any("RL 사이저" in m and "수동" in m for m in deps.notifier.msgs)


def test_rl_train_failure_notifies_and_returns_error(project, settings, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("터짐")

    monkeypatch.setattr(rl_train_mod, "train_sizer", boom)
    deps = _deps(project, settings)
    r = jobs.rl_train_body(deps)
    assert r["status"] == "error" and "터짐" in r["error"]
    assert any("RL 학습 실패" in m for m in deps.notifier.msgs)


def test_rl_train_insufficient_data_is_silent(project, settings, monkeypatch):
    monkeypatch.setattr(
        rl_train_mod, "train_sizer",
        lambda *a, **k: {"status": "insufficient_data", "promote_reason": "데이터 부족"},
    )  # fmt: skip
    deps = _deps(project, settings)
    assert jobs.rl_train_body(deps)["status"] == "insufficient_data"
    assert deps.notifier.msgs == []


def test_rl_job_registered(project):
    schedule = load("schedule", build_registry(discover()), path=project / "config" / "x.yaml")
    ctx = SimpleNamespace(gates={"market": lambda job: None})
    got = {
        j.id: j
        for j in build([FEATURE], schedule=schedule, settings=None, markets=["KR", "US"], ctx=ctx)
    }
    assert got["rl_train"].cron == {"hour": 10, "minute": 30, "day_of_week": "sat"}
    assert jobs.RL_TRAIN in FEATURE.jobs and qj.misfire_grace_for(jobs.RL_TRAIN) is not None


def test_status_has_rl_sizer_line(conn):
    ctx = commands.CommandContext(
        conn=conn, settings=None, state=None, now=lambda: NOW, notifier=Sent(),
        features=[FEATURE],
    )  # fmt: skip
    assert any("rl_sizer 없음" in line for line in status.render(ctx))
