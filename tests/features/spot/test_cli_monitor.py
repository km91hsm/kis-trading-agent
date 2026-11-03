"""`trader spot monitor --once` (옛 `tests/test_cli_monitor.py` 에서 이식).

치환: `["monitor", …]` → `["spot", "monitor", …]`, `cli._build_brokers/_build_market` →
`AccountsPortImpl.brokers/market_client`(포트에 가짜를 끼운다)."""

from decimal import Decimal

import yaml
from typer.testing import CliRunner

from tests.features.spot.conftest import make_account
from trader.features.accounts.ports import AccountsPortImpl
from trader.features.accounts.settings import to_broker_account
from trader.kernel.broker.fake import FakeBroker, FakeMarket
from trader.kernel.broker.models import Cash
from trader.kernel.cli.main import app


def test_cli_monitor_once(project, monkeypatch):
    (project / "config" / "accounts.yaml").write_text(
        yaml.safe_dump(
            {
                "accounts": [
                    {
                        "id": "paper-main",
                        "env": "paper",
                        "cano": "00000000",
                        "markets": ["KR"],
                        "mode": "auto",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    cash = {"KR": Cash(currency="KRW", available=Decimal(1), total=Decimal(1))}
    broker = FakeBroker(to_broker_account(make_account("paper-main", "paper", "auto")), cash=cash)
    monkeypatch.setattr(AccountsPortImpl, "brokers", lambda self: [broker])
    monkeypatch.setattr(
        AccountsPortImpl,
        "market_client",
        lambda self: FakeMarket(prices={"005930": Decimal("70000")}),
    )
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    r = CliRunner().invoke(app, ["spot", "monitor", "--market", "KR", "--once"])
    assert r.exit_code == 0, r.stdout
    assert '"checked": 0' in r.stdout
    assert CliRunner().invoke(app, ["spot", "monitor", "--market", "XX", "--once"]).exit_code == 1


def test_cli_monitor_fills_us_exchanges_from_ledger(project, monkeypatch):
    """S4: `trader spot monitor` 도 `ticker_exchanges` 로 `MonitorContext.exchanges` 를 채운다."""
    from trader.features.spot import ledger as spot_ledger
    from trader.features.spot import monitor as monitor_mod
    from trader.kernel.ledger import db
    from trader.kernel.registry import discover
    from trader.kernel.registry.contracts import all_migrations

    (project / "config" / "accounts.yaml").write_text(
        yaml.safe_dump(
            {
                "accounts": [
                    {"id": "paper-main", "env": "paper", "cano": "00000000",
                     "markets": ["US"], "mode": "auto"}
                ]
            }
        ),
        encoding="utf-8",
    )  # fmt: skip
    c = db.connect()
    db.init_schema(c, all_migrations(discover()))
    spot_ledger.upsert_ticker_exchange(c, "US", "KO", "NYS")
    c.close()
    monkeypatch.setattr(AccountsPortImpl, "brokers", lambda self: [])
    monkeypatch.setattr(AccountsPortImpl, "market_client", lambda self: FakeMarket())
    seen = {}

    def fake_once(ctx, *, market, today):
        seen["ex"] = dict(ctx.exchanges)
        return {"checked": 0}

    monkeypatch.setattr(monitor_mod, "monitor_once", fake_once)
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    r = CliRunner().invoke(app, ["spot", "monitor", "--market", "US", "--once"])
    assert r.exit_code == 0, r.stdout
    assert seen["ex"] == {"KO": "NYS"}
