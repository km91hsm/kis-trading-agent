"""승인 경로 테스트 공용 헬퍼: 승인 대기 요청 1건을 실제 실행 경로로 만든다."""

from datetime import UTC, datetime
from decimal import Decimal

from tests.features.accounts.conftest import make_account
from tests.features.spot.conftest import PRICE, D
from tests.features.spot.helpers import ok_decision
from trader.features.accounts import modes
from trader.features.accounts.ports import AccountsPortImpl
from trader.features.accounts.settings import to_broker_account
from trader.features.spot import executor
from trader.features.spot import ledger as spot_ledger
from trader.features.spot.schemas import Decision
from trader.kernel.broker.fake import FakeBroker
from trader.kernel.broker.models import Cash
from trader.kernel.config import Secrets


class Sent:
    """알림 수집기(Notifier 흉내)."""

    def __init__(self):
        self.msgs: list[str] = []

    def send(self, text, *, escape=True):
        self.msgs.append(text)
        return True


def fake_brokers(accounts) -> dict:
    return {
        a.id: FakeBroker(
            to_broker_account(a),
            cash={
                "KR": Cash(
                    currency="KRW", available=Decimal("10000000"), total=Decimal("10000000")
                ),
            },
        )
        for a in accounts
    }


def make_port(accounts, brokers) -> AccountsPortImpl:
    """주어진 FakeBroker 를 돌려주는 계좌 포트."""
    port = AccountsPortImpl(accounts, Secrets())
    port.brokers = lambda: brokers  # type: ignore[method-assign]
    return port


def make_pending(
    conn, settings, accounts, brokers, *, account_id="paper-main", now=None, notifier=None
) -> int:
    """approve 모드 계좌의 승인 요청을 만들고 id 를 돌려준다. notifier 는 승인 알림을 받는다."""
    acct = next(a for a in accounts if a.id == account_id)
    modes.set_account_mode(conn, acct, "approve")
    ectx = executor.exec_ctx_from(
        settings,
        conn,
        make_port(accounts, brokers),
        notifier or Sent(),
        now or (lambda: datetime.now(UTC)),
        None,
    )
    dec = Decision(**ok_decision())
    spot_ledger.ingest_checkpoint(
        conn, D, "KR", dec.ticker, "30_decision", {"data": dec.model_dump()}
    )
    res = executor.execute(ectx, dec, date=D, market="KR", price=PRICE, dry_run=False)
    return next(r for r in res if r["account_id"] == account_id)["approval_id"]


__all__ = ["Sent", "fake_brokers", "make_account", "make_pending", "make_port"]
