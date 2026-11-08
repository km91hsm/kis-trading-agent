"""단타 계좌별 스위치: 저장값 출처·실전 실효(자동+LIVE)·일시정지."""

from trader.features.accounts import api as accounts_api
from trader.features.accounts import modes
from trader.features.scalp import switch
from trader.kernel.ledger.control import set_flag


def _acc(accounts, id_):
    return next(a for a in accounts if a.id == id_)


def test_stored_defaults_paper_follows_yaml_real_off(conn, settings, accounts):
    assert switch.stored(conn, settings, _acc(accounts, "paper-main")) == (True, "yaml")
    assert switch.stored(conn, settings, _acc(accounts, "real-1")) == (False, "default")


def test_stored_control_overrides_and_dict_value(conn, settings, accounts):
    val = {"enabled": True, "confirm": "real-1", "at": "2026-11-09T00:00:00+00:00", "actor": "x"}
    set_flag(conn, switch.flag_key("real-1"), val)
    assert switch.stored(conn, settings, _acc(accounts, "real-1")) == (True, "control")
    set_flag(conn, switch.flag_key("paper-main"), False)
    assert switch.stored(conn, settings, _acc(accounts, "paper-main")) == (False, "control")


def test_stored_non_bool_values_are_not_truthy_by_accident(conn, settings, accounts):
    paper = _acc(accounts, "paper-main")
    for bad in ("false", 1, "true", 0, {"enabled": "yes"}, {"enabled": 1}, {}):
        set_flag(conn, switch.flag_key("paper-main"), bad)
        assert switch.stored(conn, settings, paper) == (False, "control"), bad
    set_flag(conn, switch.flag_key("paper-main"), {"enabled": True})
    assert switch.stored(conn, settings, paper) == (True, "control")


def test_real_enabled_requires_auto_and_live(conn, settings, accounts, accounts_port):
    real = _acc(accounts, "real-1")
    assert switch.account_enabled(conn, settings, real, accounts_port) == (False, "꺼짐")
    set_flag(conn, switch.flag_key("real-1"), True)
    ok, reason = switch.account_enabled(conn, settings, real, accounts_port)
    assert not ok and "live_confirmed" in reason
    modes.set_account_mode(conn, real, "auto", confirm=accounts_api.LIVE_CONFIRM)
    assert switch.account_enabled(conn, settings, real, accounts_port) == (True, None)
    modes.set_account_mode(conn, real, "approve")
    assert switch.account_enabled(conn, settings, real, accounts_port) == (
        False,
        "실전: 계좌 모드가 자동이 아님(approve)",
    )


def test_paused_blocks_all(conn, settings, accounts, accounts_port):
    modes.set_paused(conn, True)
    for a in accounts:
        set_flag(conn, switch.flag_key(a.id), True)
        assert switch.account_enabled(conn, settings, a, accounts_port) == (False, "전체 일시정지")


def test_any_enabled_by_market(conn, settings, accounts_port):
    assert switch.any_enabled(conn, settings, accounts_port, "US")
    set_flag(conn, switch.flag_key("paper-main"), False)
    assert not switch.any_enabled(conn, settings, accounts_port, "US")
    assert switch.any_enabled(conn, settings, accounts_port, "KR")  # paper-two
