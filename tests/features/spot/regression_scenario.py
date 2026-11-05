"""현물 회귀 스냅샷 시나리오(옛 `tests/stock_regression_scenario.py` 이식).

현물 기본 경로(asset 기본값)의 판정·체크포인트·주문 행을 시각·id 같은 바뀌는 값을 뺀 dict 로
돌려준다. 골든은 `tests/fixtures/stock_regression_v1.json`(합성 데이터)."""

from __future__ import annotations

from decimal import Decimal

from tests.features.accounts.conftest import make_account
from tests.features.spot.conftest import PRICE, T0, D
from tests.features.spot.helpers import ok_decision
from trader.features.accounts.ports import AccountsPortImpl
from trader.features.accounts.settings import to_broker_account
from trader.features.spot import executor
from trader.features.spot import ledger as spot_ledger
from trader.features.spot.schemas import Decision
from trader.kernel.broker.fake import FakeBroker
from trader.kernel.broker.models import Cash
from trader.kernel.config import Secrets
from trader.kernel.session.run_store import read_checkpoint, run_dir

VOLATILE = {"created_at", "updated_at", "received_at", "requested_at", "id", "decided_at", "ts"}


def _clean(v):
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in sorted(v.items()) if k not in VOLATILE}
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return v


class RegressionPort(AccountsPortImpl):
    """합성 계좌 paper-main(auto)·real-main(off) + FakeBroker(네트워크 없음)."""

    def __init__(self) -> None:
        super().__init__(
            [
                make_account("paper-main", "paper", "auto", ["KR", "US"]),
                make_account("real-main", "real", "off", ["KR"]),
            ],
            Secrets(),
        )

    def brokers(self):
        cash = {
            "KR": Cash(currency="KRW", available=Decimal("10000000"), total=Decimal("10000000")),
            "US": Cash(currency="USD", available=Decimal("10000"), total=Decimal("10000")),
        }
        return [FakeBroker(to_broker_account(a), cash=dict(cash)) for a in self.accounts()]

    def market_client(self):
        return None


def run(settings, conn, accounts_port) -> dict:
    """현재 TRADER_HOME 의 원장·state 를 써서 시나리오를 돌린다(네트워크 없음)."""
    ectx = executor.exec_ctx_from(settings, conn, accounts_port, None, lambda: T0, None)
    out: dict = {}
    for name, dec in (
        ("buy", ok_decision()),
        ("hold", ok_decision(ticker="000660", action="HOLD", target_weight=0.0)),
    ):
        d = Decision(**dec)
        spot_ledger.ingest_checkpoint(
            conn, D, "KR", d.ticker, "30_decision", {"data": d.model_dump()}
        )
        res = executor.execute(ectx, d, date=D, market="KR", price=PRICE, dry_run=False)
        out[name] = {
            "results": _clean(res),
            "checkpoints": {
                st: _clean((read_checkpoint(run_dir(D, "KR", d.ticker), st) or {}).get("data"))
                for st in ("40_risk_check_paper-main", "40_risk_check_real-main",
                           "50_order_paper-main")
            },
        }  # fmt: skip
    out["orders"] = [
        dict(r)
        for r in conn.execute(
            "SELECT account_id, ticker, market, side, qty, price, order_type, status, mode, "
            "order_no FROM orders ORDER BY id"
        )
    ]
    out["risk_checks"] = conn.execute("SELECT COUNT(*) FROM risk_checks").fetchone()[0]
    return out
