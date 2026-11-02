"""내장 telegram_poll 본체: outbox 재전송 → getUpdates 1회."""

import logging
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from trader.kernel.daemon import builtin_jobs as bj
from trader.kernel.daemon.runner import DaemonDeps

NOW = datetime(2026, 10, 27, 1, 0, tzinfo=UTC)


class Notifier:
    def __init__(self, log):
        self.log = log

    def flush_outbox(self, *, now=None):
        self.log.append(("flush", now))
        return {"sent": 0, "left": 0, "dropped": 0}

    def send(self, text, *, escape=True):
        return True


def _deps(tmp_path: Path, notifier, **kw) -> DaemonDeps:
    return DaemonDeps(
        settings=SimpleNamespace(notify=SimpleNamespace(enabled=True)),
        schedule=None, conn_factory=lambda: sqlite3.connect(":memory:"), state=tmp_path,
        now=lambda: NOW, markets=[], gates={}, stop_event=threading.Event(), dry_run=False,
        notifier=notifier, secrets=SimpleNamespace(), **kw,
    )  # fmt: skip


def test_telegram_body_flushes_outbox_then_polls(tmp_path, monkeypatch):
    log: list = []
    monkeypatch.setattr(bj, "make_receiver", lambda s, **kw: "recv")
    monkeypatch.setattr(
        bj.commands, "poll_once", lambda ctx, r: log.append(("poll", r)) or {"handled": 0}
    )
    deps = _deps(tmp_path, Notifier(log))
    assert bj.telegram_body(deps, None) == {"handled": 0}
    assert log == [("flush", NOW), ("poll", "recv")]


def test_telegram_body_skips_without_receiver(tmp_path, monkeypatch, caplog):
    log: list = []
    monkeypatch.setattr(bj, "make_receiver", lambda s, **kw: None)

    def boom(*, now=None):
        raise RuntimeError("x")

    notifier = Notifier(log)
    notifier.flush_outbox = boom  # 재전송 예외는 삼킨다
    with caplog.at_level(logging.WARNING, logger="trader.daemon"):
        out = bj.telegram_body(_deps(tmp_path, notifier), None)
    assert out == {"skipped": "텔레그램 미설정"}


def test_telegram_body_passes_ports_to_commands(tmp_path, monkeypatch):
    seen: list = []
    monkeypatch.setattr(bj, "make_receiver", lambda s, **kw: "recv")
    monkeypatch.setattr(bj.commands, "poll_once", lambda ctx, r: seen.append(ctx) or {})
    feats = [SimpleNamespace(id="f")]
    deps = _deps(tmp_path, Notifier([]), ports={"p": object()}, features=feats)
    bj.telegram_body(deps, None)
    assert seen[0].ports is deps.ports and seen[0].features == feats
    assert seen[0].notifier is deps.notifier
