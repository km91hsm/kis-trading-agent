from decimal import Decimal

import yaml
from typer.testing import CliRunner

from trader.features.accounts import modes
from trader.features.accounts.settings import load_accounts
from trader.kernel.cli.main import app
from trader.kernel.config.secrets import Secrets
from trader.kernel.ledger import db


def _accounts(project, extra=()):
    data = {
        "accounts": [
            {"id": "paper-main", "env": "paper", "cano": "0", "markets": ["KR"], "mode": "auto"},
            {"id": "real-main", "env": "real", "cano": "1", "markets": ["KR"], "mode": "off"},
            *extra,
        ]
    }
    (project / "config" / "accounts.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def _by_id(id_):
    return next(a for a in load_accounts(secrets=Secrets()) if a.id == id_)


def test_accounts_mode_sets_and_shows(project):
    _accounts(project)
    runner = CliRunner()
    r = runner.invoke(app, ["accounts", "mode", "paper-main", "approve"])
    assert r.exit_code == 0, r.stdout
    conn = db.connect()
    assert modes.get_account_mode(conn, _by_id("paper-main")) == "approve"
    conn.close()
    r = runner.invoke(app, ["accounts", "show"])
    assert (
        r.exit_code == 0
        and "paper-main" in r.stdout
        and "approve" in r.stdout
        and "일시정지" in r.stdout
    )


def test_accounts_mode_real_auto_requires_confirm(project):
    _accounts(project)
    runner = CliRunner()
    r = runner.invoke(app, ["accounts", "mode", "real-main", "auto"])
    assert r.exit_code == 1 and "LIVE" in (r.stdout + (r.stderr or ""))
    conn = db.connect()
    assert conn.execute("SELECT COUNT(*) FROM account_modes").fetchone()[0] == 0
    conn.close()
    r = runner.invoke(app, ["accounts", "mode", "real-main", "auto", "--confirm", "LIVE"])
    assert r.exit_code == 0
    conn = db.connect()
    assert modes.live_confirmed(conn, "real-main")
    conn.close()


def test_accounts_mode_rejects_unknown(project):
    _accounts(project)
    runner = CliRunner()
    assert runner.invoke(app, ["accounts", "mode", "nope", "auto"]).exit_code == 1
    assert runner.invoke(app, ["accounts", "mode", "paper-main", "fast"]).exit_code == 1


def test_accounts_pause_resume(project):
    _accounts(project)
    runner = CliRunner()
    assert runner.invoke(app, ["accounts", "pause"]).exit_code == 0
    conn = db.connect()
    assert modes.is_paused(conn)
    conn.close()
    assert runner.invoke(app, ["accounts", "resume"]).exit_code == 0
    conn = db.connect()
    assert not modes.is_paused(conn)
    conn.close()


def test_accounts_show_reports_effective_mode(project):
    _accounts(project)
    conn = db.connect()
    from trader.kernel.registry import discover
    from trader.kernel.registry.contracts import all_migrations

    db.init_schema(conn, all_migrations(discover()))
    conn.execute("INSERT INTO account_modes VALUES ('real-main','auto',NULL,'t')")
    conn.commit()
    conn.close()
    r = CliRunner().invoke(app, ["accounts", "show"])
    assert r.exit_code == 0, r.stdout
    line = next(x for x in r.stdout.splitlines() if x.startswith("real-main"))
    assert "off" in line and "live_confirmed" in line


def test_accounts_pause_reports_config_error_as_one_line(project):
    (project / "config" / "accounts.yaml").write_text("accounts: [{id: x}]", encoding="utf-8")
    r = CliRunner().invoke(app, ["accounts", "pause"])
    assert (
        r.exit_code == 1
        and "오류:" in (r.stdout + (r.stderr or ""))
        and "Traceback" not in (r.stdout + (r.stderr or ""))
    )


def test_accounts_show_real_fo_account_is_off(project):
    _accounts(
        project,
        extra=[
            {
                "id": "real-fo",
                "env": "real",
                "cano": "2",
                "acnt_prdt_cd": "03",
                "markets": ["KR"],
                "mode": "approve",
                "asset": "fo",
            }
        ],
    )
    r = CliRunner().invoke(app, ["accounts", "show"])
    assert r.exit_code == 0, r.stdout
    line = next(x for x in r.stdout.splitlines() if x.startswith("real-fo"))
    assert "off" in line and "approve" not in line.split("—")[0]


def test_ping_counts_missing_keys_as_failures(project):
    _accounts(project)
    r = CliRunner().invoke(app, ["accounts", "ping"])
    assert (
        r.exit_code == 1
        and "[실패] 시세 클라이언트" in r.stdout
        and "paper-main" in r.stdout
        and "실패 3건" in r.stdout
    )


def test_ping_account_filter(project, monkeypatch):
    _accounts(project)
    from trader.features.accounts import cli as acli
    from trader.kernel.broker.fake import FakeMarket

    monkeypatch.setattr(
        acli,
        "build_market",
        lambda s, a: FakeMarket(prices={"005930": Decimal("71500"), "AAPL": Decimal("227.55")}),
    )
    monkeypatch.setattr(acli, "build_brokers", lambda s, a, **kw: [])
    r = CliRunner().invoke(app, ["accounts", "ping", "--account", "nope"])
    assert r.exit_code == 1 and "찾을 수 없습니다" in (r.stdout + (r.stderr or ""))
    r = CliRunner().invoke(app, ["accounts", "ping", "--account", "paper-main"])
    assert (
        "real-main" not in r.stdout and "[OK] 005930 현재가" in r.stdout and "실패 1건" in r.stdout
    )
