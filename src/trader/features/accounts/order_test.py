"""현물 주문 연결 점검(`trader accounts order-test`, 사용자만 실행·모의 전용, P16).

1주 지정가 매수(현재가 −10%, `broker.ticks.quantize_price` 로 호가단위 정렬)를 접수하고 즉시
취소한다. 점검 도구라 executor 코어를 지나지 않고 원장(`orders`)에도 적지 않는다 — 브로커
주문·취소 직접 호출은 `tests/kernel/registry/test_order_paths.py` 의 `ALLOWED` 예외(fo
`order_test` 와 같다)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from trader.kernel.broker.models import OrderRequest
from trader.kernel.broker.ticks import quantize_price

DEFAULT_TICKER = {"KR": "005930", "US": "AAPL"}  # 기본 점검 종목(US 는 NAS)
DISCOUNT = Decimal("0.9")  # 현재가 −10% — 체결되지 않을 지정가


def run(
    broker: Any, current: Decimal, *, account: str, market: str, ticker: str,
    exchange: str | None,
) -> dict:  # fmt: skip
    """접수 → 즉시 취소. 결과 `{account, market, ticker, price, order_id, placed, cancelled,
    cancel_error}`(접수 실패면 `error` 도). 취소 실패는 `cancel_error` 에 담는다(예외를 올리지
    않음)."""
    price = quantize_price(market, Decimal(current) * DISCOUNT)
    out: dict = {
        "account": account, "market": market, "ticker": ticker, "price": str(price),
        "order_id": None, "placed": False, "cancelled": False, "cancel_error": None,
    }  # fmt: skip
    req = OrderRequest(
        ticker=ticker, market=market, side="BUY", qty=1, order_type="LIMIT", price=price,
        exchange=exchange,
    )  # fmt: skip
    try:
        placed = broker.place_order(req)
    except Exception as e:  # noqa: BLE001 - 점검 도구는 모든 실패를 보고한다
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    out["order_id"] = placed.order_no
    out["placed"] = bool(placed.accepted and placed.order_no)
    if not out["placed"]:
        out["error"] = placed.message or "주문번호 없음"
        return out
    try:
        c = broker.cancel_order(
            placed.order_no, market=market, org_no=placed.org_no, ticker=ticker,
            exchange=exchange, qty=1,
        )  # fmt: skip
        out["cancelled"] = bool(c.accepted)
        if not c.accepted:
            out["cancel_error"] = c.message or "취소 거부"
    except Exception as e:  # noqa: BLE001 - 취소 실패도 결과로 보고한다(주문이 살아 있을 수 있음)
        out["cancel_error"] = f"{type(e).__name__}: {e}"
    return out
