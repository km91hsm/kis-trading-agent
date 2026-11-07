import os

from tests.conftest import secret_env_names
from trader.kernel.config.secrets import load_secrets


def test_secret_env_names_cover_fields_and_account_keys(monkeypatch):
    monkeypatch.setenv("KIS_MY_ACCT_APP_KEY", "x")
    monkeypatch.setenv("KIS_UNRELATED", "x")
    names = secret_env_names()
    assert {"TELEGRAM_BOT_TOKEN", "KIS_PAPER_APP_KEY", "KIS_MY_ACCT_APP_KEY"} <= names
    assert "KIS_UNRELATED" not in names


def test_autouse_fixture_clears_ambient_secrets(project):
    assert not (secret_env_names() & set(os.environ))
    assert load_secrets().TELEGRAM_BOT_TOKEN is None


def test_autouse_fixture_isolates_global_daemon_lock(tmp_path_factory):
    from pathlib import Path

    from trader.kernel.daemon.lock import global_lock_path

    path = global_lock_path()
    assert path.parent == Path(os.environ["TRADER_GLOBAL_LOCK_DIR"])
    assert not path.is_relative_to(Path.home() / ".trader")
    assert path.is_relative_to(tmp_path_factory.getbasetemp())
