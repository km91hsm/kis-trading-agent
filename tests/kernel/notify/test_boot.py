"""notify.boot.boot_notifier: 부트 결과로 알림기(시크릿 없음·조립 실패면 NullNotifier)."""

from types import SimpleNamespace

from trader.kernel.config.secrets import Secrets
from trader.kernel.notify import telegram
from trader.kernel.notify.boot import boot_notifier
from trader.kernel.notify.telegram import NullNotifier


def _boot(secrets, enabled=True):
    return SimpleNamespace(
        secrets=secrets, settings=SimpleNamespace(notify=SimpleNamespace(enabled=enabled))
    )


def test_no_secrets_gives_null_notifier():
    assert isinstance(boot_notifier(_boot(None)), NullNotifier)
    assert isinstance(boot_notifier(SimpleNamespace(settings=None)), NullNotifier)


def test_passes_enabled_flag_and_settings_none_means_enabled(monkeypatch):
    seen = []
    sent = object()
    monkeypatch.setattr(telegram, "make_notifier", lambda s, **kw: seen.append(kw) or sent)
    assert boot_notifier(_boot(Secrets(), enabled=False)) is sent
    assert boot_notifier(SimpleNamespace(secrets=Secrets(), settings=None)) is sent
    assert seen == [{"enabled": False}, {"enabled": True}]


def test_build_failure_falls_back_to_null(monkeypatch, caplog):
    def boom(*a, **k):
        raise ValueError("깨짐")

    monkeypatch.setattr(telegram, "make_notifier", boom)
    with caplog.at_level("WARNING", logger="trader.notify"):
        assert isinstance(boot_notifier(_boot(Secrets())), NullNotifier)
    assert "알림 조립 실패" in caplog.text
