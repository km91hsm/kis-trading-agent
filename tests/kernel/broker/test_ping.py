from datetime import UTC, date, datetime
from decimal import Decimal

from trader.kernel.broker.errors import KisApiError
from trader.kernel.broker.fake import FakeBroker, FakeFoBroker, FakeMarket
from trader.kernel.broker.models import BrokerAccount, Cash, FoBalance, FoCash
from trader.kernel.broker.ping import failures, run_checks

from .conftest import make_account

TODAY = date(2026, 10, 9)
KR_US = ["KR", "US"]


def _market(fail=()):
    return FakeMarket(
        prices={"005930": Decimal("71500"), "AAPL": Decimal("227.55")}, fail=set(fail)
    )


def _broker(account, fail=()):
    return FakeBroker(
        account,
        cash={
            "KR": Cash(currency="KRW", available=1, total=1),
            "US": Cash(currency="USD", available=1, total=1),
        },
        fail=set(fail),
    )


def _accounts():
    return [make_account("paper-main", "paper", KR_US), make_account("real-main", "real")]


def test_run_checks_all_ok():
    accts = _accounts()
    checks = run_checks(_market(), [_broker(a) for a in accts], accts, today=TODAY)
    assert failures(checks) == 0
    text = "\n".join(f"{c.label} {c.detail}" for c in checks)
    assert "005930" in text and "AAPL" in text and "paper-main" in text
    assert "개장" in text


def test_run_checks_reports_failures():
    accts = _accounts()
    checks = run_checks(
        _market(fail={"get_price_us"}), [_broker(a) for a in accts], accts, today=TODAY
    )
    assert failures(checks) == 1
    bad = [c for c in checks if not c.ok]
    assert "AAPL" in bad[0].label and bad[0].detail.startswith("KisApiError")


def test_run_checks_market_build_failure_counts():
    accts = _accounts()
    checks = run_checks(
        None,
        [_broker(a) for a in accts],
        accts,
        today=TODAY,
        market_error=KisApiError("AUTH", "x"),
    )
    assert not checks[0].ok and "시세 클라이언트" in checks[0].label
    assert "KisApiError" in checks[0].detail
    assert not any("현재가" in c.label or "휴장일" in c.label for c in checks)
    assert failures(checks) == 1


def test_run_checks_counts_account_without_broker():
    """앱키가 없어 건너뛴 계좌는 침묵이 아니라 실패로 센다."""
    accts = _accounts()
    checks = run_checks(_market(), [_broker(accts[0])], accts, today=TODAY)
    assert failures(checks) == 1
    bad = next(c for c in checks if not c.ok)
    assert bad.label == "real-main" and "앱키" in bad.detail


def test_run_checks_fo_account_summary():
    now = datetime(2026, 10, 9, tzinfo=UTC)
    acct = BrokerAccount(id="paper-fo", env="paper", cano="00000000", asset="fo")
    cash = FoCash(
        deposit=Decimal("1"),
        orderable_cash=Decimal("900"),
        orderable_total=Decimal("900"),
        margin_total=Decimal("0"),
        add_margin=Decimal("0"),
        nav=Decimal("1000"),
        realized_today=Decimal("0"),
        unrealized=Decimal("0"),
        requested_at=now,
        received_at=now,
    )
    fo = FakeFoBroker(acct, balance=FoBalance(positions=[], unverified=[], cash=cash))
    checks = run_checks(_market(), [fo], [acct], today=TODAY)
    assert failures(checks) == 0
    c = next(c for c in checks if "paper-fo" in c.label)
    assert "NAV 1000 KRW" in c.detail and "주문가능 900" in c.detail and "보유 0건" in c.detail
