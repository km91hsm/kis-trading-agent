"""scan_analyze.post 섀도 기록 훅: `_scan` 체크포인트 → 전략 행."""

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tests.kernel.daemon.test_runner import make_deps
from trader.features.evaluation import shadow
from trader.features.evaluation.feature import FEATURE
from trader.features.evaluation.jobs import shadow_record_hook
from trader.features.regime.api import HedgeCfg
from trader.kernel.broker.fake import FakeMarket
from trader.kernel.broker.models import Candle
from trader.kernel.session import run_store

NOW = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)  # 월요일 10:00 KST
SCAN = {"candidates": [{"ticker": "005930", "score": 0.9}], "session": {"fake": False}}


def _deps(project, **kw):
    candles = [
        Candle(
            date=date(2026, 10, 26) + timedelta(days=i),
            open=Decimal(1), high=Decimal(1), low=Decimal(1), close=Decimal(100 + i), volume=5,
        )
        for i in range(5)
    ]  # fmt: skip
    market = FakeMarket(candles={"069500": candles})
    kw.setdefault("settings", SimpleNamespace(hedge=HedgeCfg()))
    return make_deps(
        project, ports={"accounts": SimpleNamespace(market_client=lambda: market)}, **kw
    )


def _write_scan(deps, scan):
    d = run_store.market_dir(run_store.run_date_for("KR", NOW), "KR", deps.state, create=True)
    (d / "_scan.json").write_text(json.dumps(scan), encoding="utf-8")


def _rows(deps):
    c = deps.conn_factory()
    try:
        return shadow.rows(c, market="KR")
    finally:
        c.close()


def test_hook_records_from_scan_checkpoint(project, monkeypatch):
    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    deps = _deps(project)
    _write_scan(deps, SCAN)
    shadow_record_hook(deps=deps, market="KR", result={"targets": 1, "ok": ["005930"]})
    got = {r["strategy"]: r["weights"] for r in _rows(deps)}
    for name, w in {
        "candidates_1n": {"005930": 1.0},
        "quant_topn": {"005930": 1.0},
        "llm": {},
        "llm_all": {},
    }.items():
        assert got[name] == w
    assert "index_hold" in got  # 지수 proxy 종가표가 있으면 지수 기준선도


def test_hook_skips_fake_and_partial(project, monkeypatch):
    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    deps = _deps(project)
    # scan 체크포인트 없음
    shadow_record_hook(deps=deps, market="KR", result={"targets": 1})
    assert _rows(deps) == []
    # scan.session.fake
    _write_scan(deps, {**SCAN, "session": {"fake": True}})
    shadow_record_hook(deps=deps, market="KR", result={"targets": 1})
    assert _rows(deps) == []
    # FAKE_CLAUDE 환경
    _write_scan(deps, SCAN)
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    shadow_record_hook(deps=deps, market="KR", result={"targets": 1})
    assert _rows(deps) == []
    monkeypatch.delenv("FAKE_CLAUDE")
    # 전부 실패(ok 없음)
    shadow_record_hook(deps=deps, market="KR", result={"failed": ["005930"]})
    assert _rows(deps) == []
    # 일부 실패: 기록하되 실패 종목은 후보 기준선에서 뺀다
    shadow_record_hook(deps=deps, market="KR", result={"failed": ["000660"], "ok": ["005930"]})
    assert {r["strategy"] for r in _rows(deps)} >= {"candidates_1n", "llm"}


@pytest.mark.parametrize(
    "result", [{"skipped": "x"}, {"targets": 0, "error": "e"}, {"stopped": True}]
)
def test_shadow_hook_ignores_incomplete_results(project, monkeypatch, result):
    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    deps = _deps(project)
    _write_scan(deps, SCAN)
    shadow_record_hook(deps=deps, market="KR", result=result)
    assert _rows(deps) == []


def test_hook_failure_is_only_a_warning(project, monkeypatch, caplog):
    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    deps = _deps(project, settings=SimpleNamespace())  # hedge 속성 없음 → 예외
    _write_scan(deps, SCAN)
    shadow_record_hook(deps=deps, market="KR", result={"targets": 1})
    assert "섀도 포트폴리오 기록 실패" in caplog.text


def test_feature_registers_post_hook():
    assert FEATURE.hooks[0].point == "scan_analyze.post"
    assert FEATURE.hooks[0].fn is shadow_record_hook
