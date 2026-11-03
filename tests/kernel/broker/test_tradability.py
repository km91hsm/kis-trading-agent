"""매매 불가·경고 종목 판정과 rules 의 킬스위치·trade_block 거부 (옛 test_stock_lessons 발췌)."""

from datetime import date
from decimal import Decimal

import pytest

from tests.features.spot.helpers import ok_decision
from tests.features.spot.test_rules import _eval, _portfolio, _pos
from trader.kernel.broker import tradability


def test_rules_killswitch_blocks_new_buys_only():
    buy = ok_decision(action="BUY", target_weight=0.1)
    v = _eval(buy, killswitch="누적 손실 −16%")
    assert not v.approved and any("킬스위치" in r for r in v.reasons)
    sell = ok_decision(action="SELL", target_weight=0.0, ticker="000660")
    pf = _portfolio(positions={"000660": _pos()}, held_since={"000660": date(2026, 10, 1)})
    v2 = _eval(sell, pf, killswitch="누적 손실 −16%")
    assert not any("킬스위치" in r for r in v2.reasons)
    hedge = _eval(buy, is_hedge=True, killswitch="x")
    assert not any("킬스위치" in r for r in hedge.reasons)


# --- 매매 불가·경고 종목 --------------------------------------------------------


@pytest.mark.parametrize(
    ("quote", "expected"),
    [
        ({"status_code": "51"}, "관리종목"),
        ({"status_code": "58"}, "거래정지"),
        ({"warn_code": "02"}, "투자경고"),
        ({"warn_code": "03"}, "투자위험"),
        ({"halted": True}, "임시정지"),
        ({"status_code": "55", "warn_code": "00"}, None),  # 55 신용가능 — 정상
        ({}, None),
        (None, None),
    ],
)
def test_block_reason(quote, expected):
    got = tradability.block_reason(quote)
    assert (got is None) if expected is None else (expected in got)


def test_rules_trade_block_rejects_buy_not_sell():
    v = _eval(ok_decision(action="BUY", target_weight=0.1), trade_block="투자경고 종목")
    assert not v.approved and any("매매 제한" in r for r in v.reasons)
    pf = _portfolio(positions={"000660": _pos()}, held_since={"000660": date(2026, 10, 1)})
    v2 = _eval(
        ok_decision(action="SELL", target_weight=0.0, ticker="000660"),
        pf,
        trade_block="투자경고 종목",
    )
    assert not any("매매 제한" in r for r in v2.reasons)


# --- 실전 자본 상한 ------------------------------------------------------------


def test_block_reason_accepts_quote_object():
    from trader.kernel.broker.models import Quote

    q = Quote(
        ticker="005930",
        market="KR",
        price=Decimal(1),
        change_pct=0.0,
        volume=1,
        asof="2026-10-27T00:00:00+00:00",
        status_code="58",
    )
    assert tradability.block_reason(q) == "거래정지"
