"""spot 데몬 monitor 가 ml 포트의 close_table 을 MonitorContext 에 주입한다(M8 Ruling A6)."""

from dataclasses import replace
from types import SimpleNamespace

from tests.features.spot.test_jobs_hedge import JOB, _hedge_deps
from trader.features.spot import jobs as spot_jobs
from trader.features.spot import monitor as monitor_mod


def _run(deps, monkeypatch):
    seen = {}

    def fake(ctx, **kw):
        seen["ctx"] = ctx
        return {"checked": 0}

    monkeypatch.setattr(monitor_mod, "monitor_once", fake)
    spot_jobs.monitor_body(deps, JOB)
    return seen["ctx"]


def test_daemon_monitor_injects_close_table_from_ml_port(project, settings, monkeypatch):
    def fn(market, since, until):
        return {}

    deps = _hedge_deps(project, settings, closes=[100] * 30, prices={})
    deps = replace(deps, ports={**deps.ports, "ml": SimpleNamespace(close_table=fn)})
    assert _run(deps, monkeypatch).close_table is fn


def test_daemon_monitor_close_table_none_without_ml_port(project, settings, monkeypatch):
    deps = _hedge_deps(project, settings, closes=[100] * 30, prices={})
    assert _run(deps, monkeypatch).close_table is None
