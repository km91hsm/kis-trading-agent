from typer.testing import CliRunner

from trader.kernel.cli import main as main_mod
from trader.kernel.notify.telegram import Update


class FakeReceiver:
    last_update_id = None

    def __init__(self):
        self.n = 0

    def poll(self, offset=None):
        self.n += 1
        return [Update(update_id=self.n, text="/status", chat_id="1")]


class Sent:
    def __init__(self):
        self.msgs = []

    def send(self, text, *, escape=True):
        self.msgs.append(text)
        return True


def test_cli_telegram_poll_without_secrets_exits_1(project):
    r = CliRunner().invoke(main_mod.app, ["telegram", "poll"])
    assert r.exit_code == 1 and "토큰" in (r.stdout + (r.stderr or ""))


def test_cli_notify_test_null_notifier_succeeds(project):
    r = CliRunner().invoke(main_mod.app, ["notify-test"])
    assert r.exit_code == 0 and "전송 성공" in r.stdout
    assert "텔레그램 미설정·꺼짐 — 실제 전송 없음" in r.stdout


def test_cli_notify_test_failure_exits_1(project, monkeypatch):
    class Bad:
        def send(self, text, *, escape=True):
            return False

    monkeypatch.setattr(main_mod, "make_notifier", lambda s, **kw: Bad())
    assert CliRunner().invoke(main_mod.app, ["notify-test"]).exit_code == 1
