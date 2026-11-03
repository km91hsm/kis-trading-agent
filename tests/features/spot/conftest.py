"""spot 테스트 공용 픽스처·상수."""

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tests.features.accounts.conftest import make_account  # noqa: F401
from trader.features.accounts import api as accounts_api
from trader.features.accounts.ports import AccountsPortImpl
from trader.features.evaluation.ports import EvaluationPortImpl
from trader.features.regime import ports as regime_ports
from trader.features.scan import ports as scan_ports
from trader.features.spot import executor
from trader.kernel.cli.boot import build_registry
from trader.kernel.config import Secrets
from trader.kernel.config.loader import load
from trader.kernel.ledger import db
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations

D = date(2026, 10, 9)
T0 = datetime(2026, 10, 9, 1, 0, tzinfo=UTC)
PRICE = Decimal("71500")


@pytest.fixture
def settings(project):
    """settings.yaml 이 없으면 기본값."""
    return load("settings", build_registry(discover()), path=project / "config" / "settings.yaml")


@pytest.fixture
def conn(project):
    c = db.connect()
    db.init_schema(c, all_migrations(discover()))
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def accounts_port():
    return AccountsPortImpl([], Secrets())


@pytest.fixture
def ports(settings, accounts_port):
    """레인이 받는 포트 dict 모양(`Boot.ports`). scan·evaluation 은 실제 구현체(evaluation 은
    `Boot.ports` 처럼 같은 dict 를 지연 참조한다)."""
    out = {
        "accounts": accounts_port,
        "regime": regime_ports.build_port(SimpleNamespace(settings=settings)),
        "scan": scan_ports.build_port(SimpleNamespace(settings=settings)),
        "ml": None,  # 기본은 ml 포트 없음 — 필요한 테스트가 `ml_port` 를 끼운다
    }
    out["evaluation"] = EvaluationPortImpl(settings, build_registry(discover()), out)
    return out


@pytest.fixture
def ml_port():
    """가짜 ml 포트(`MlPort` 모양). 승격 모델 1개·피처 행 1개를 내고 스냅샷 호출을 기록한다."""
    calls: list = []

    def build_report(model, meta, row, *, ticker, asof):
        return {
            "ticker": ticker, "asof": asof, "role": "quant", "model": "q-test-v1",
            "p_up": 0.5, "p_flat": 0.3, "p_down": 0.2, "signal": "BUY", "horizon_days": 5,
            "features_used": ["ret_5d"],
        }  # fmt: skip

    def snapshot(markets, *, tickers=None):
        calls.append((list(markets), tickers))
        return len(tickers or [])

    return SimpleNamespace(
        quant_enabled=lambda: True,
        latest_model=lambda market: (object(), {"version": "q-test-v1"}),
        latest_row=lambda ticker, market, *, asof: {"ret_5d": 0.01},
        build_report=build_report,
        snapshot=snapshot,
        peer_medians=lambda **kw: None,
        calls=calls,
    )


class Notes:
    def __init__(self):
        self.sent: list[str] = []

    def send(self, text: str, *, escape: bool = True) -> bool:
        self.sent.append(text)
        return True


def ectx(ports, settings, conn, accounts, brokers, **kw) -> executor.ExecContext:
    """포트를 실은 ExecContext(계좌 정책·일시정지는 accounts 기능, now=T0)."""
    return executor.ExecContext(
        settings=settings,
        accounts=accounts,
        conn=conn,
        brokers=brokers,
        policy=accounts_api.effective_mode,
        paused=accounts_api.is_paused,
        notifier=kw.pop("notifier", None) or Notes(),
        now=lambda: T0,
        ports=ports,
        **kw,
    )
