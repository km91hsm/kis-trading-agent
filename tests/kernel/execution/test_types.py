from decimal import Decimal

import pytest

from trader.kernel.execution import types
from trader.kernel.registry import contracts


def _intent(**kw):
    base = {
        "account_id": "a", "ticker": "005930", "market": "KR", "exchange": None, "side": "BUY",
        "qty": 3, "order_type": "LIMIT", "price": Decimal("70000"), "origin": "llm",
        "risk_stage": "40_risk", "order_stage": "50_order", "idem_key": "1|a|BUY|50_order",
        "decision_id": 1, "name": "삼성전자", "opinion": {"x": 1},
    }  # fmt: skip
    base.update(kw)
    return types.OrderIntent(**base)


def test_intent_request_carries_only_order_fields():
    i = _intent()
    req = i.request()
    assert (req.ticker, req.market, req.side, req.qty) == ("005930", "KR", "BUY", 3)
    assert (req.order_type, req.price, req.exchange) == ("LIMIT", Decimal("70000"), None)
    with pytest.raises(AttributeError):
        i.qty = 5  # frozen


def test_policy_transmits_and_constants():
    def p(mode="auto", paused=False, dry_run=False):
        return types.ExecPolicy(mode=mode, note=None, paused=paused, dry_run=dry_run)

    assert p().transmits
    assert not p(mode="off").transmits
    assert not p(paused=True).transmits
    assert not p(dry_run=True).transmits
    assert contracts.ORIGIN_CHECK is types.ORIGIN_CHECK
    assert types.DEAD_STATUSES == ("rejected", "cancelled", "failed", "expired")
    assert types.dead_status_sql() == "'rejected','cancelled','failed','expired'"
    gp = types.GatePlan(approved=False)
    assert gp.reasons == [] and gp.intents == [] and gp.verdict_payload == {}


def test_policy_transmits_risk_exit_ignores_paused():
    def p(mode="auto", paused=True, dry_run=False, risk_exit=False):
        return types.ExecPolicy(
            mode=mode, note=None, paused=paused, dry_run=dry_run, risk_exit=risk_exit
        )

    assert p(risk_exit=True).transmits
    assert not p(risk_exit=True, mode="off").transmits
    assert not p(risk_exit=True, dry_run=True).transmits
    assert not p().transmits  # risk_exit 기본값 False


def test_bookkeeping_error_message():
    e = types.OrderBookkeepingError("0001", ValueError("디스크"))
    assert e.order_no == "0001" and "접수번호 0001" in str(e) and "디스크" in str(e)
    assert "접수번호 -" in str(types.OrderBookkeepingError(None, ValueError("x")))


def test_open_statuses_constant():
    from trader.kernel.execution.types import DEAD_STATUSES, OPEN_STATUSES

    assert OPEN_STATUSES == ("accepted", "unknown", "partial")
    assert not set(OPEN_STATUSES) & set(DEAD_STATUSES)
