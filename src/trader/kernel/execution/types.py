"""주문 실행 DTO·상수: 주문 의도(`OrderIntent`)·실행 정책(`ExecPolicy`)·게이트 계획(`GatePlan`).

이 모듈은 `broker.models`·`config`·표준 라이브러리만 import 한다(원장 DDL 이 ORIGIN_CHECK 를 쓴다).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Any

from trader.kernel.broker.models import OrderRequest

# orders.origin CHECK 허용값 — 주문의 실행 종류. 기능의 origin 선언도 이 안이어야 한다.
ORIGIN_CHECK = (
    "llm", "stop", "hedge", "scalp", "rebalance", "reprice", "park", "external", "fo",
)  # fmt: skip

# 살아 있지 않은 주문 상태 — 중복 방지·당일 매수·주문금액·보유 폴백이 모두 이 목록을 쓴다.
# expired 는 체결 동기화가 마감 뒤 체결 없는 accepted·unknown 주문에 붙인다.
DEAD_STATUSES = ("rejected", "cancelled", "failed", "expired")
# 아직 살아 있는(체결·취소를 기다리는) 주문 상태 — 미체결 집계·일일 리포트가 쓴다.
OPEN_STATUSES = ("accepted", "unknown", "partial")

# 승인 요청 유효 시간
APPROVAL_TTL = timedelta(minutes=30)
# 선점했지만 브로커로 보내지도 승인 요청을 만들지도 못한 실행 키를 넘겨받는 나이
EXEC_KEY_STALE = timedelta(minutes=10)


def dead_status_sql(col: str = "status") -> str:
    """`{col} NOT IN (…)` 에 넣을 목록 문자열(컬럼명 인자는 호환용 — 목록만 돌려준다)."""
    return ",".join(f"'{s}'" for s in DEAD_STATUSES)


@dataclass(frozen=True)
class OrderIntent:
    """게이트를 통과한 주문 1건의 의도. 코어가 멱등 claim → 승인/전송 → 기록을 맡는다."""

    account_id: str
    ticker: str
    market: str
    exchange: str | None
    side: str
    qty: int
    order_type: str
    price: Decimal | None
    origin: str
    risk_stage: str
    order_stage: str
    idem_key: str
    decision_id: int | None
    quote_price: Decimal | None = None
    name: str | None = None
    opinion: dict | None = None
    asset: str = "stock"

    def request(self) -> OrderRequest:
        """브로커 주문 요청(OrderRequest 는 extra=forbid 라 해당 필드만 넘긴다)."""
        return OrderRequest(
            ticker=self.ticker,
            market=self.market,  # type: ignore[arg-type]
            side=self.side,  # type: ignore[arg-type]
            qty=self.qty,
            order_type=self.order_type,  # type: ignore[arg-type]
            price=self.price,
            exchange=self.exchange,
        )


@dataclass(frozen=True)
class ExecPolicy:
    """계좌 1개의 실행 정책(유효 모드·일시정지·dry-run·위험 청산 여부). risk_exit 면 일시정지여도
    전송한다(off·dry_run 은 그대로 막는다)."""

    mode: str
    note: str | None
    paused: bool
    dry_run: bool
    risk_exit: bool = False  # 위험 청산 — 일시정지여도 전송(off·dry_run 은 그대로 막는다)

    @property
    def transmits(self) -> bool:
        """브로커로 실제 전송하는가."""
        return self.mode != "off" and not self.dry_run and (self.risk_exit or not self.paused)


@dataclass
class GatePlan:
    """게이트 판정 결과. spot 은 계좌마다 intent 0~1개, fo 는 N 다리."""

    approved: bool
    reasons: list[str] = field(default_factory=list)
    intents: list[OrderIntent] = field(default_factory=list)
    verdict_payload: dict[str, Any] = field(default_factory=dict)


class OrderBookkeepingError(RuntimeError):
    """브로커가 접수한 뒤 원장 기록이 실패했다. order_no 로 수동 대조한다."""

    def __init__(self, order_no: str | None, cause: Exception) -> None:
        super().__init__(f"주문 기록 실패 (접수번호 {order_no or '-'}): {cause}")
        self.order_no = order_no
