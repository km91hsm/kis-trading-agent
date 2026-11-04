"""파생 세션 입력(`fo/inputs.py`, 옛 `tests/fo/test_inputs.py` 7건) — 절마다 asof·received_at, 체인
호출 ≤18, 절 실패는 errors, 과거 asof 는 historical, 서브에이전트 묶음 형태. A8: 뉴스·거시 절은
M11 이연(`news_macro.error`), 시장 뷰는 regime 포트."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from tests.features.fo._support import (
    ACCT,
    NOW,
    D,
    _ctx,
    account,
    ports_for,
    settings,
)
from trader.features.fo import executor, inputs
from trader.kernel.broker.fo import CONTRACT_VERSION
from trader.kernel.ledger import control
from trader.kernel.session.run_store import market_dir, read_checkpoint


def test_inputs_sections_have_asof_and_received_at(conn):
    ctx = _ctx(conn)
    inp = inputs.build_inputs(ctx, today=D, asof=NOW, accounts=ctx.accounts)
    for sec in ("technical", "sentiment", "news_macro", "context"):
        assert inp[sec]["asof"] == NOW.isoformat() and inp[sec]["received_at"], sec
    tech = inp["technical"]["data"]
    assert tech["meta"]["code"] == "105W11" and tech["quote"]["data"]["price"] == "350.5"
    assert tech["daily"]["data"][-1]["close"] == "370" and tech["indicators"]["data"]["sma_20"]
    assert inp["context"]["data"]["index"] == {"price": "350", "asof": NOW.isoformat()}
    accts = inp["context"]["data"]["accounts"]
    assert accts[0]["id"] == ACCT and accts[0]["nav"] == "100000000"
    assert inp["context"]["data"]["limits"]["max_leverage"] == 2.0
    assert inp["meta"]["historical"] is False and inp["meta"]["request_id"]
    assert inp["meta"]["proxy"] == "KOSPI200"
    # 뉴스·거시 절은 M11(InputsPort.recent) 이연 — 그 오류만 남고 파생 절은 오류 없음(A8)
    assert inp["news_macro"]["data"] is None
    assert inp["news_macro"]["error"] == "M11: InputsPort.recent"
    assert all(k.startswith("news_macro") for k in inp["errors"]), inp["errors"]
    p = inputs.save_inputs(inp, today=D, state=None)
    assert p.name == "_fo_inputs.json"
    saved = read_checkpoint(market_dir(D, "KR"), "_fo_inputs")
    assert saved["data"]["meta"]["date"] == D.isoformat()


def test_chain_calls_le_18(conn):
    """옵션 체인은 ATM ± 4 행사가(9개 × 콜·풋 = 18 시세)까지만 요청한다."""
    ctx = _ctx(conn)
    inp = inputs.build_inputs(ctx, today=D, asof=NOW, accounts=ctx.accounts)
    chain = inp["context"]["data"]["option_chain"]
    assert 0 < len(chain) <= 18
    strikes = {c["strike"] for c in chain}
    assert len(strikes) <= 9 and "350" in strikes
    assert ctx.market_client.calls.count("get_option_chain") == 1


def test_section_failure_goes_to_errors(conn):
    from tests.features.fo._support import _market

    ctx = _ctx(conn, market=_market(fail={"get_fo_daily", "get_kospi200"}))
    inp = inputs.build_inputs(ctx, today=D, asof=NOW, accounts=ctx.accounts)
    tech = inp["technical"]["data"]
    assert tech["daily"]["error"] and tech["indicators"]["error"] == "no daily candles"
    assert tech["quote"]["error"] is None
    assert "technical.daily" in inp["errors"] and "context.index" in inp["errors"]
    assert inp["context"]["data"]["index"]["price"] is None
    # 지수 절이 비면 시세의 기초지수로 채우지 않는다(지수 신선도는 응답 시각 기준) — 체인은 그래도
    assert inp["context"]["data"]["option_chain"] == [] and "context.option_chain" in inp["errors"]


def test_historical_asof_flagged(conn):
    ctx = _ctx(conn)
    old = NOW - timedelta(seconds=settings().fo.max_input_age_s + 1)
    inp = inputs.build_inputs(ctx, today=D, asof=old, accounts=ctx.accounts)
    assert inp["meta"]["historical"] is True and inp["meta"]["asof"] == old.isoformat()
    assert inp["technical"]["data"]["daily"]["data"][-1]["date"] <= old.date().isoformat()


def test_bundle_analyst_news_macro_shape(conn):
    """A8: news_macro 묶음은 거시 원문 없이 proxy·국면·시장 뷰와 이연 사유만 담는다(analyst-market
    md 가 ticker 를 proxy 로 적도록 proxy 를 남긴다)."""
    ctx = _ctx(conn)
    inp = inputs.build_inputs(ctx, today=D, asof=NOW, accounts=ctx.accounts)
    b = inputs.bundle(inp, kind="analyst", role="news_macro")
    assert b["scope"] == "fo" and b["proxy"] == "KOSPI200" and b["market"] == "KR"
    assert b["regime"] == inp["context"]["data"]["regime"]
    assert b["market_view"] == inp["context"]["data"]["market_view"]
    assert b["error"] == "M11: InputsPort.recent"


def test_bundle_technical_shape(conn):
    ctx = _ctx(conn)
    inp = inputs.build_inputs(ctx, today=D, asof=NOW, accounts=ctx.accounts)
    b = inputs.bundle(inp, kind="analyst", role="technical")
    assert set(b["technical"]) == {"quote", "daily", "minute", "orderbook", "indicators"}
    assert b["ticker"] == "KOSPI200" and b["asof"] == NOW.isoformat() and b["role"] == "technical"
    s = inputs.bundle(inp, kind="analyst", role="sentiment")
    assert "investor_flow" in s["sentiment"] and s["quote"]["data"]["price"] == "350.5"
    r = inputs.bundle(inp, kind="researcher", round=0, side="bull", reports=[{"role": "technical"}])
    assert r["scope"] == "fo" and r["reports"] == [{"role": "technical"}] and r["side"] == "bull"
    assert r["own_previous"] is None and r["regime"] == inp["context"]["data"]["regime"]
    t = inputs.bundle(
        inp, kind="trader", reports=[{"role": "technical"}], summary={"rounds": 0},
        position={"id": 1, "version": 2},
    )  # fmt: skip
    assert t["context"]["position"] == {"id": 1, "version": 2} and t["request_id"]
    assert t["context"]["accounts"][0]["id"] == ACCT
    assert t["research_summary"] == {"rounds": 0}
    assert "option_chain" in t["context"] and "limits" in t["context"]


def test_context_has_regime_view_verified(conn):
    ctx = _ctx(conn)
    control.set_flag(conn, "regime:KR", {"state": "bear", "count": 3, "signals": {}})
    control.set_flag(
        conn, "market_view:KR", {"date": D.isoformat(), "stance": "RISK_OFF", "confidence": 0.8}
    )
    key = executor.verified_key(settings(), account(), "FUTURES")
    control.set_flag(conn, key, CONTRACT_VERSION)
    inp = inputs.build_inputs(ctx, today=D, asof=NOW, accounts=ctx.accounts)
    c = inp["context"]["data"]
    assert c["regime"]["state"] == "bear" and c["market_view"]["state"] == "RISK_OFF"
    assert c["market_view"]["stance"] == "RISK_OFF"  # regime 포트 view_today(A8)
    assert c["limits"]["regime_cap"] == 2.0  # leverage_by_regime 미설정 → max_leverage
    assert c["verified"] == {ACCT: {"FUTURES": True, "OPTION_BUY": False, "OPTION_SELL": False}}


# --- 옛 test_review_s3 #5·#6 ---------------------------------------------------------------


def test_spot_hedge_held_from_ports(conn):
    """현물 헤지 보유 = regime 포트 `hedge_tickers("KR")` ∩ accounts 포트 `brokers()` 의 KR 보유.
    `brokers()` 는 리스트(현 구현)·dict(Protocol) 둘 다 받는다. fo 브로커는 읽지 않는다."""
    ctx = _ctx(conn)
    held = SimpleNamespace(ticker="114800", qty=3)
    other = SimpleNamespace(ticker="005930", qty=1)
    spot = SimpleNamespace(
        account=SimpleNamespace(id="paper-main", markets=["KR"]),
        get_positions=lambda m: [held, other] if m == "KR" else [],
    )
    ports = dict(ctx.ports)
    acc = {**vars(ctx.ports["accounts"]), "brokers": lambda: [spot]}
    ports["accounts"] = SimpleNamespace(**acc)
    ports["regime"] = SimpleNamespace(
        **{k: getattr(ctx.ports["regime"], k) for k in ("load", "view_today")},
        hedge_tickers=lambda m: frozenset({"114800", "252670"}),
    )
    ctx.ports = ports
    inp = inputs.build_inputs(ctx, today=D, asof=NOW, accounts=ctx.accounts)
    assert inp["context"]["data"]["spot_exposure"]["hedge_held"] == ["114800"]
    ports["accounts"].brokers = lambda: {"paper-main": spot}
    inp = inputs.build_inputs(ctx, today=D, asof=NOW, accounts=ctx.accounts)
    assert inp["context"]["data"]["spot_exposure"]["hedge_held"] == ["114800"]
    # 호출자가 넘긴 값이 우선
    inp = inputs.build_inputs(ctx, today=D, asof=NOW, accounts=ctx.accounts, spot_hedge_held=["x"])
    assert inp["context"]["data"]["spot_exposure"]["hedge_held"] == ["x"]


def test_spot_hedge_held_empty_without_ports(conn):
    ctx = _ctx(conn)
    ctx.ports = ports_for(ctx.settings, ctx.accounts)  # brokers() → {} (현물 브로커 없음)
    inp = inputs.build_inputs(ctx, today=D, asof=NOW, accounts=ctx.accounts)
    assert inp["context"]["data"]["spot_exposure"]["hedge_held"] == []
