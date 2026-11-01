"""registry.hooks: 포인트별 묶음(기능 순서 유지)과 실패 격리 실행."""

import logging

from trader.kernel.registry import hooks
from trader.kernel.registry.types import Feature, Hook


def _ok(**kw):
    return {"got": kw}


def _boom(**kw):
    raise RuntimeError("터짐")


def _a(**kw):
    return "a"


def _b(**kw):
    return "b"


def test_by_point_groups_in_feature_order():
    f1 = Feature(
        id="f1", tier="service",
        hooks=(Hook("news.post_ingest", _a), Hook("reflect.mature", _b)),
    )  # fmt: skip
    f2 = Feature(id="f2", tier="service", hooks=(Hook("news.post_ingest", _b),))
    out = hooks.by_point([f1, f2])
    assert [h.fn for h in out["news.post_ingest"]] == [_a, _b]
    assert [h.fn for h in out["reflect.mature"]] == [_b]
    assert isinstance(out["news.post_ingest"], tuple)


def test_run_isolates_failures_and_reports_each(caplog):
    by = {"news.post_ingest": (Hook("news.post_ingest", _boom), Hook("news.post_ingest", _ok))}
    with caplog.at_level(logging.ERROR, logger="trader.registry"):
        res = hooks.run(by, "news.post_ingest", x=1)
    assert len(res) == 2
    assert res[0]["hook"].endswith("_boom") and res[0]["error"] == "RuntimeError: 터짐"
    assert res[1] == {"hook": f"{_ok.__module__}._ok", "result": {"got": {"x": 1}}}
    assert any(r.name == "trader.registry" for r in caplog.records)


def test_run_without_hooks_returns_empty():
    assert hooks.run(None, "news.post_ingest") == []
    assert hooks.run({}, "news.post_ingest") == []
