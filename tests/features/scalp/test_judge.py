"""scalp 판정자: 스레드 풀·클램프·데몬 단일체·에이전트 등록."""

import threading
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from trader.features.briefing.briefing import AGENT as BRIEFING_AGENT
from trader.features.scalp import judge
from trader.features.scalp.feature import FEATURE
from trader.features.scalp.schemas import ScalpAdvice, ScalpVerdict
from trader.features.scalp.settings import ScalpCfg
from trader.features.scalp.state import advice_key
from trader.kernel.ledger import db
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations
from trader.kernel.session import agents

NOW = datetime(2026, 11, 4, 1, 0, tzinfo=UTC)
GO = ScalpVerdict(
    go=True,
    size="half",
    take_profit_pct=0.02,
    stop_pct=0.02,
    max_hold_min=120,
    reason="돌파 확인",
    confidence=0.7,
)


@pytest.fixture(autouse=True)
def _reset_judge():
    judge.shutdown_judge()
    yield
    judge.shutdown_judge()


def _deps(settings):
    def factory():
        c = db.connect()
        db.init_schema(c, all_migrations(discover()))
        return c

    return SimpleNamespace(
        settings=settings, conn_factory=factory, stop_event=threading.Event(), now=lambda: NOW
    )


def test_clamp_cannot_widen_stop_or_hold():
    cfg = ScalpCfg(enabled=True, judge=True)
    out = judge.clamp_verdict(GO, cfg)
    assert out["stop_pct"] == cfg.stop_pct and out["max_hold_min"] == cfg.max_hold_min
    assert out["take_profit_pct"] == 0.02  # 0.5~2배 안이면 판정값
    low = judge.clamp_verdict(GO.model_copy(update={"take_profit_pct": 0.001}), cfg)
    assert low["take_profit_pct"] == cfg.take_profit_pct * 0.5
    assert judge.limits(cfg) == {
        "take_profit_pct": cfg.take_profit_pct,
        "stop_pct": cfg.stop_pct,
        "max_hold_min": cfg.max_hold_min,
    }


def test_advice_key_is_per_position():
    a = advice_key("KR", {"ticker": "005930", "entry_at": "2026-10-24T01:00:00+00:00"})
    b = advice_key("KR", {"ticker": "005930", "entry_at": "2026-10-25T01:00:00+00:00"})
    assert a != b and a.startswith("hold:KR:005930:")


def test_judge_expires_unpolled_submissions():
    """회수되지 않은 판정(후보에서 빠진 종목)은 ttl 뒤 버린다."""
    t = [0.0]
    j = judge.ScalpJudge(lambda payload, schema: GO, ttl_s=60, clock=lambda: t[0])
    try:
        j.submit("entry:a", {}, ScalpVerdict)
        j._futs["entry:a"].result(timeout=5)
        t[0] = 61.0
        j.submit("entry:b", {}, ScalpVerdict)
        assert "entry:a" not in j._futs and "entry:a" not in j._at
        assert j.poll("entry:a") is None
        j._futs["entry:b"].result(timeout=5)
        assert j.poll("entry:b") == GO and j._at == {}
    finally:
        j.shutdown()


def test_judge_for_returns_none_when_stop_event_set(project, settings):
    deps = _deps(settings)
    j = judge.judge_for(deps)
    assert judge.judge_for(deps) is j  # 데몬 수명 동안 하나
    deps.stop_event.set()
    assert j._ask({"ticker": "005930"}, ScalpVerdict) is None


def test_judge_for_uses_fake_under_fake_claude(monkeypatch, project, settings):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    j = judge.judge_for(_deps(settings))
    v = j._ask({"ticker": "005930"}, ScalpVerdict)
    assert isinstance(v, ScalpVerdict) and v.go is False and v.size == "half"
    a = j._ask({"ticker": "005930"}, ScalpAdvice)
    assert isinstance(a, ScalpAdvice) and a.action == "hold"


def test_agent_registered_with_common_rules():
    assert FEATURE.agents == (judge.AGENT,)
    loaded = agents.load_agent(judge.AGENT)
    assert loaded.model == "haiku"
    rules = agents.common_rules(loaded.prompt)
    assert len(rules) == 6
    assert rules == agents.common_rules(agents.load_agent(BRIEFING_AGENT).prompt)


class _Regime:
    def load(self, conn, market):
        return {"state": "neutral", "count": 2}

    def view_today(self, conn, market, today):
        raise RuntimeError("판단 없음")


class _Book:
    asks = [(f"{100 + i}", 10 + i) for i in range(7)]
    bids = [(f"{99 - i}", 20 + i) for i in range(7)]


def test_judge_context_sections_and_failures():
    class Client:
        def get_orderbook_kr(self, ticker):
            return _Book()

        def get_orderbook_us(self, ticker, exchange):
            raise RuntimeError("호가 실패")

    ports, kw = {"regime": _Regime()}, {"ticker": "005930", "today": NOW.date()}
    kr = judge.judge_context(None, ports, Client(), market="KR", exchange=None, **kw)
    assert len(kr["orderbook"]["asks"]) == 5 and kr["orderbook"]["bids"][0] == ["99", 20]
    assert kr["news"] == [] and kr["regime"] == {"state": "neutral", "count": 2}
    assert kr["market_view"] is None  # 실패한 절은 None
    us = judge.judge_context(None, ports, Client(), market="US", exchange="NAS", **kw)
    assert us["orderbook"] is None


def test_judge_context_regime_passes_stored_record(conn, settings):
    """T4-R1: 국면 절은 저장된 기록 그대로 — 없으면 None/None(‘bull’/0 으로 기울이지 않는다)."""
    from trader.features.regime import regime as regime_mod
    from trader.features.regime.ports import RegimePortImpl
    from trader.kernel.ledger import control

    class Client:
        def get_orderbook_kr(self, ticker):
            raise RuntimeError("호가 없음")

    ports = {"regime": RegimePortImpl(settings)}
    kw = {"market": "KR", "ticker": "005930", "exchange": None, "today": NOW.date()}
    out = judge.judge_context(conn, ports, Client(), **kw)
    assert out["regime"] == {"state": None, "count": None}
    control.set_flag(conn, f"{regime_mod.FLAG_PREFIX}KR", {"state": "bear", "count": 3})
    out = judge.judge_context(conn, ports, Client(), **kw)
    assert out["regime"] == {"state": "bear", "count": 3}
