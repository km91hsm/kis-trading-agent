"""KIS 점검: 시세·휴장일·계좌별 현금(파생은 잔고)을 순서대로 확인한다. 출력·종료코드는 호출자 몫."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from trader.kernel.broker.fake import FakeMarket
from trader.kernel.broker.market import KisMarket
from trader.kernel.broker.models import BrokerAccount


@dataclass(frozen=True)
class Check:
    label: str
    ok: bool
    detail: str  # 성공: 결과 문자열, 실패: "<예외 이름>: <메시지>"


def _run(label: str, fn: Callable[[], object]) -> Check:
    try:
        return Check(label, True, str(fn()))
    except Exception as e:  # noqa: BLE001 - 점검 도구는 모든 실패를 보고한다
        return Check(label, False, f"{type(e).__name__}: {e}")


def _cash_detail(broker, market: str) -> str:
    c = broker.get_cash(market)  # 모의 2/s 한도를 고려해 한 번만 호출한다
    return f"{c.available} {c.currency}"


def _fo_detail(broker) -> str:
    bal = broker.get_balance()
    nav = "없음" if bal.cash.nav is None else f"{bal.cash.nav} KRW"
    return (
        f"NAV {nav}, 주문가능 {bal.cash.orderable_cash} KRW, "
        f"보유 {len(bal.positions)}건, 미확정 {len(bal.unverified)}건"
    )


def run_checks(
    market: KisMarket | FakeMarket | None,
    brokers: list,
    accounts: list[BrokerAccount],
    *,
    today: date,
    market_error: Exception | None = None,
) -> list[Check]:
    """점검 순서: 시세 클라이언트 → 휴장일 → 005930 → AAPL → 계좌별(브로커 없는 계좌는 실패 1건)."""
    checks: list[Check] = []
    if market is None:
        e = market_error
        detail = f"{type(e).__name__}: {e}" if e is not None else "시세 클라이언트가 없습니다"
        checks.append(Check("시세 클라이언트", False, detail))
    else:
        checks.append(
            _run(f"휴장일 {today}", lambda: "휴장" if market.is_holiday(today) else "개장")
        )
        checks.append(_run("005930 현재가", lambda: market.get_price_kr("005930").price))
        checks.append(_run("AAPL 현재가", lambda: market.get_price_us("AAPL", "NAS").price))

    built = {b.account.id for b in brokers}
    for a in accounts:
        if a.id not in built:
            checks.append(Check(a.id, False, f"{a.env} 앱키·시크릿이 없어 점검하지 못했습니다"))
    for broker in brokers:
        acct = broker.account
        if acct.asset == "fo":
            checks.append(_run(f"{acct.id} 파생 잔고", lambda b=broker: _fo_detail(b)))
            continue
        for m in acct.markets:
            checks.append(_run(f"{acct.id} {m} 현금", lambda b=broker, m=m: _cash_detail(b, m)))
    return checks


def failures(checks: list[Check]) -> int:
    return sum(1 for c in checks if not c.ok)
