import pytest

from tests.features.accounts.conftest import make_account
from trader.features.accounts import modes
from trader.features.accounts.settings import Account


def _fo(id, env, mode):
    return Account(
        id=id, env=env, cano="00000000", acnt_prdt_cd="03", markets=["KR"], mode=mode, asset="fo"
    )


def test_schema_has_accounts_tables(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"accounts", "account_modes"} <= names


def test_mode_default_from_yaml_then_control(conn):
    acc = make_account("paper-main", "paper", "auto")
    assert modes.get_account_mode(conn, acc) == "auto" and modes.mode_source(conn, acc.id) == "yaml"
    modes.set_account_mode(conn, acc, "off")
    assert modes.get_account_mode(conn, acc) == "off"
    assert modes.mode_source(conn, acc.id) == "control"


def test_real_auto_requires_confirm(conn):
    acc = make_account("real-main", "real", "off")
    with pytest.raises(modes.ConfirmationRequired):
        modes.set_account_mode(conn, acc, "auto")
    assert conn.execute("SELECT COUNT(*) FROM account_modes").fetchone()[0] == 0
    modes.set_account_mode(conn, acc, "auto", confirm="LIVE")
    assert modes.get_account_mode(conn, acc) == "auto"
    assert modes.live_confirmed(conn, "real-main")["confirm"] == "LIVE"
    modes.set_account_mode(conn, acc, "off")
    assert modes.live_confirmed(conn, "real-main") is None  # 내리면 확인 기록 삭제


def test_unknown_mode_rejected(conn):
    with pytest.raises(ValueError):
        modes.set_account_mode(conn, make_account("p", "paper", "auto"), "yolo")


def test_same_value_does_not_touch_updated_at(conn):
    acc = make_account("p", "paper", "auto")
    modes.set_account_mode(conn, acc, "approve")
    t1 = conn.execute("SELECT updated_at FROM account_modes WHERE account_id='p'").fetchone()[0]
    modes.set_account_mode(conn, acc, "approve")
    t2 = conn.execute("SELECT updated_at FROM account_modes WHERE account_id='p'").fetchone()[0]
    assert t2 == t1


def test_effective_and_account_mode(conn):
    real = make_account("real-main", "real", "approve")
    assert modes.account_mode(conn, real) == ("approve", None)
    conn.execute("INSERT INTO account_modes VALUES ('real-main','auto',NULL,'t')")
    conn.commit()
    assert modes.effective_mode(conn, real) == ("off", modes.LIVE_UNCONFIRMED_NOTE)
    assert modes.account_mode(conn, _fo("real-fo", "real", "approve"))[0] == "off"
    assert modes.account_mode(conn, _fo("paper-fo", "paper", "auto"))[0] == "auto"


def test_sync_accounts_upserts(conn):
    a = make_account("p", "paper", "auto", markets=["KR", "US"])
    modes.sync_accounts(conn, [a])
    modes.sync_accounts(conn, [a.model_copy(update={"mode": "approve"})])
    row = conn.execute(
        "SELECT env, asset, markets_json, yaml_mode FROM accounts WHERE id='p'"
    ).fetchone()
    assert tuple(row) == ("paper", "stock", '["KR", "US"]', "approve")
    assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1


def test_paused_flag(conn):
    assert modes.is_paused(conn) is False
    modes.set_paused(conn, True)
    assert modes.is_paused(conn) is True
    modes.set_paused(conn, False)
    assert modes.is_paused(conn) is False
