"""`trader spot approvals …`·`trader telegram poll` (브로커는 포트에 가짜로 끼운다).

옛 `tests/test_cli_approvals.py` 에서 이식. 치환: `cli._build_brokers` → `AccountsPortImpl.brokers`,
`cli.make_notifier/make_receiver` → `trader.kernel.cli.main.*`, `approvals` → `spot approvals`."""

from datetime import UTC, datetime

import pytest
import yaml
from typer.testing import CliRunner

from tests.features.spot.approval_helpers import Sent, fake_brokers, make_account, make_pending
from trader.features.accounts import modes
from trader.features.accounts.ports import AccountsPortImpl
from trader.kernel.cli import main as cli_main
from trader.kernel.cli.main import app
from trader.kernel.execution import ledger as xledger
from trader.kernel.ledger import control, db
from trader.kernel.notify.commands import OFFSET_FLAG
from trader.kernel.notify.telegram import Update
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations


class FakeReceiver:
    def __init__(self, updates):
        self.updates = updates
        self.offsets = []

    def poll(self, offset=None):
        self.offsets.append(offset)
        return [u for u in self.updates if offset is None or u.update_id >= offset]


@pytest.fixture
def env(project, monkeypatch, settings):
    (project / "config" / "accounts.yaml").write_text(
        yaml.safe_dump(
            {
                "accounts": [
                    {
                        "id": "paper-main",
                        "env": "paper",
                        "cano": "00000000",
                        "markets": ["KR"],
                        "mode": "approve",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    accounts = [make_account("paper-main", "paper", "approve")]
    brokers = fake_brokers(accounts)
    monkeypatch.setattr(AccountsPortImpl, "brokers", lambda self: brokers)
    sent = Sent()
    monkeypatch.setattr(cli_main, "make_notifier", lambda *a, **k: sent)
    conn = db.connect()
    db.init_schema(conn, all_migrations(discover()))
    aid = make_pending(conn, settings, accounts, brokers, now=lambda: datetime.now(UTC))
    conn.close()
    return brokers["paper-main"], sent, aid


def test_cli_approvals_flow(env):
    broker, _sent, aid = env
    runner = CliRunner()
    r = runner.invoke(app, ["spot", "approvals", "list"])
    assert r.exit_code == 0 and f"#{aid}" in r.stdout and "005930" in r.stdout
    r = runner.invoke(app, ["spot", "approvals", "approve", str(aid)])
    assert r.exit_code == 0 and "FAKE-1" in r.stdout and len(broker.orders) == 1
    r = runner.invoke(app, ["spot", "approvals", "approve", str(aid)])
    assert r.exit_code == 1  # 이미 처리됨
    r = runner.invoke(app, ["spot", "approvals", "reject", "999"])
    assert r.exit_code == 1


def test_cli_approvals_paused_keeps_pending(env):
    broker, _sent, aid = env
    conn = db.connect()
    modes.set_paused(conn, True)
    conn.close()
    r = CliRunner().invoke(app, ["spot", "approvals", "approve", str(aid)])
    assert r.exit_code == 1 and broker.orders == []
    conn = db.connect()
    assert xledger.get_approval(conn, aid)["status"] == "pending"
    conn.close()


def test_cli_telegram_poll_processes_commands(env, monkeypatch):
    broker, sent, aid = env
    receiver = FakeReceiver(
        [
            Update(update_id=5, text="/status", chat_id="1"),
            Update(update_id=6, text=f"/reject {aid}", chat_id="1"),
            Update(update_id=7, text="잡담", chat_id="1"),
        ]
    )
    monkeypatch.setattr(cli_main, "make_receiver", lambda *a, **k: receiver)
    runner = CliRunner()
    r = runner.invoke(app, ["telegram", "poll"])
    assert r.exit_code == 0, r.stdout
    assert "reject" in r.stdout and broker.orders == []
    conn = db.connect()
    assert xledger.get_approval(conn, aid)["status"] == "rejected"
    assert control.get_flag(conn, OFFSET_FLAG) == 8
    conn.close()
    assert any("상태" in m for m in sent.msgs)  # /status 응답
    runner.invoke(app, ["telegram", "poll"])  # 두 번째 폴링은 저장된 오프셋부터
    assert receiver.offsets[-1] == 8


def test_cli_telegram_poll_without_config_fails(env, monkeypatch):
    monkeypatch.setattr(cli_main, "make_receiver", lambda *a, **k: None)
    r = CliRunner().invoke(app, ["telegram", "poll"])
    assert r.exit_code == 1
