"""scan 테스트 공용 픽스처."""

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from trader.features.regime import ports as regime_ports
from trader.features.scan import ports as scan_ports
from trader.kernel.cli.boot import build_registry
from trader.kernel.config.loader import load
from trader.kernel.lane.types import LaneContext
from trader.kernel.ledger import db
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations

D = date(2026, 10, 9)
NOW = datetime(2026, 10, 9, 0, 0, tzinfo=UTC)


class Sent:
    """알림 수집기(`notifier.send(text)`)."""

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[str] = []
        self.fail = fail

    def send(self, text: str, *, escape: bool = True) -> bool:
        if self.fail:
            raise RuntimeError("전송 실패")
        self.sent.append(text)
        return True


def lane(
    conn, settings, *, brokers=None, accounts=(), market_client=None, now=NOW, state=None,
    notifier=None,
) -> LaneContext:  # fmt: skip
    """스캔 테스트용 LaneContext: 가짜 accounts 포트 + 실제 regime·scan 포트."""
    from trader.kernel.platform import paths

    boot = SimpleNamespace(settings=settings)
    ports = {
        "accounts": SimpleNamespace(
            brokers=lambda: brokers or {},
            accounts=lambda: list(accounts),
            market_client=lambda: market_client,
            holiday_fn=lambda: None,
        ),
        "regime": regime_ports.build_port(boot),
        "scan": scan_ports.build_port(boot),
    }
    return LaneContext(
        settings, conn, state=state or paths.state_dir(), now=lambda: now, dry_run=True,
        notifier=notifier, ports=ports,
    )  # fmt: skip


@pytest.fixture
def settings(project):
    """settings.yaml 이 없으면 기본값(`settings.theme` 포함)."""
    return load("settings", build_registry(discover()), path=project / "config" / "settings.yaml")


@pytest.fixture
def conn(project):
    c = db.connect()
    db.init_schema(c, all_migrations(discover()))
    try:
        yield c
    finally:
        c.close()
