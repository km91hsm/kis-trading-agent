import json
import stat
import sys

from typer.testing import CliRunner

from trader.features.saveticker import credentials
from trader.features.saveticker.native import NativeSession
from trader.kernel.cli.main import app
from trader.kernel.platform import paths

TRICKY = "p#a ss'w\"o\\rd=$X"


def test_save_load_clear_round_trips_tricky_password_in_private_env(project):
    assert credentials.load() is None and credentials.stored() is False
    credentials.save("user@example.com", TRICKY)
    assert credentials.load() == ("user@example.com", TRICKY)
    assert credentials.stored() is True
    env = paths.secrets_path()
    if sys.platform != "win32":  # POSIX 권한 비트
        assert stat.S_IMODE(env.stat().st_mode) == 0o600
    credentials.clear()
    assert credentials.load() is None
    assert "SAVETICKER_" not in env.read_text(encoding="utf-8")


def test_cli_login_set_status_clear_never_prints_password(project):
    runner = CliRunner()
    r = runner.invoke(
        app, ["saveticker", "login", "set"], input=f"user@example.com\n{TRICKY}\n{TRICKY}\n"
    )
    assert r.exit_code == 0, r.output
    assert TRICKY not in r.output
    assert credentials.load() == ("user@example.com", TRICKY)
    s = runner.invoke(app, ["saveticker", "login", "status"])
    assert s.exit_code == 0 and json.loads(s.output) == {"stored": True}
    assert "user@example.com" not in s.output and TRICKY not in s.output
    c = runner.invoke(app, ["saveticker", "login", "clear"])
    assert c.exit_code == 0 and credentials.load() is None


def test_cli_login_set_rejects_empty_values(project):
    r = CliRunner().invoke(app, ["saveticker", "login", "set"], input="not-an-email\nx\nx\n")
    assert r.exit_code != 0 and credentials.load() is None


def test_native_credentials_message_returns_values_only_on_request(project, tmp_path):
    session = NativeSession(tmp_path / "st")
    empty = session.handle({"id": 1, "type": "credentials"})
    assert empty["ok"] is True and empty["available"] is False
    assert "password" not in empty
    credentials.save("user@example.com", TRICKY)
    full = session.handle({"id": 2, "type": "credentials"})
    assert full == {
        "id": 2,
        "type": "credentials",
        "ok": True,
        "available": True,
        "email": "user@example.com",
        "password": TRICKY,
    }
    runtime = tmp_path / "st" / "runtime"
    for f in runtime.glob("*") if runtime.exists() else []:
        assert TRICKY not in f.read_text(encoding="utf-8", errors="replace")


def test_native_accepts_persistent_recovery_diagnostic_reasons(tmp_path):
    session = NativeSession(tmp_path)
    for reason in ("credentials_missing", "login_failed", "login_form_missing"):
        reply = session.handle(
            {
                "id": 1,
                "type": "diagnostics",
                "category": "recheck",
                "reason": reason,
                "reload_count": 4,
            }
        )
        assert reply["ok"] is True, reason


def test_password_with_interpolation_and_edge_spaces_round_trips(project):
    for pw in ("x${HOME}y", " lead", "trail ", "a'b\"c${X:-d}"):
        credentials.save("user@example.com", pw)
        assert credentials.load() == ("user@example.com", pw), pw


def test_control_characters_are_rejected(project):
    import pytest

    for pw in ("a\rb", "a\x0bb", "a\x0cb", "a\u2028b", "a\x00b"):
        with pytest.raises(ValueError):
            credentials.save("user@example.com", pw)
    assert credentials.load() is None
