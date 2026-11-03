"""내장 telegram_poll 본체: outbox 재전송 → getUpdates 1회."""

import logging
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from trader.kernel.config import LlmSettings
from trader.kernel.daemon import builtin_jobs as bj
from trader.kernel.daemon.runner import DaemonDeps
from trader.kernel.registry.types import Feature

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
        settings=SimpleNamespace(notify=SimpleNamespace(enabled=True), llm=LlmSettings()),
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

    def raising(*, now=None):
        log.append("flush-ran")
        boom()

    notifier = Notifier(log)
    notifier.flush_outbox = raising  # 재전송 예외는 삼킨다
    with caplog.at_level(logging.WARNING, logger="trader.daemon"):
        out = bj.telegram_body(_deps(tmp_path, notifier), None)
    assert out == {"skipped": "텔레그램 미설정"}
    assert log == ["flush-ran"] and "알림 재전송 실패" in caplog.text


def test_telegram_body_passes_ports_to_commands(tmp_path, monkeypatch):
    seen: list = []
    monkeypatch.setattr(bj, "make_receiver", lambda s, **kw: "recv")
    monkeypatch.setattr(bj.commands, "poll_once", lambda ctx, r: seen.append(ctx) or {})
    feats = [SimpleNamespace(id="f")]
    deps = _deps(tmp_path, Notifier([]), ports={"p": object()}, features=feats)
    bj.telegram_body(deps, None)
    assert seen[0].ports is deps.ports and seen[0].features == feats
    assert seen[0].notifier is deps.notifier


class _Conn:
    """close 호출을 기록하는 실제 연결 래퍼."""

    def __init__(self):
        self.real = sqlite3.connect(":memory:")
        self.closed = False

    def close(self):
        self.closed = True
        self.real.close()

    def __getattr__(self, name):
        return getattr(self.real, name)


def test_telegram_body_closes_connection_and_propagates_poll_error(tmp_path, monkeypatch):
    from trader.kernel.daemon.jobs import build
    from trader.kernel.daemon.runner import run_job
    from trader.kernel.ledger import connect, init_schema
    from trader.kernel.registry import discover
    from trader.kernel.registry.contracts import all_migrations

    def bad_poll(ctx, r):
        raise RuntimeError("poll 실패")

    monkeypatch.setattr(bj, "make_receiver", lambda s, **kw: "recv")
    monkeypatch.setattr(bj.commands, "poll_once", bad_poll)
    conns: list[_Conn] = []

    def factory():
        conns.append(_Conn())
        return conns[-1]

    deps = _deps(tmp_path, Notifier([]))
    deps = DaemonDeps(**{**deps.__dict__, "conn_factory": factory})
    try:
        bj.telegram_body(deps, None)
        raise AssertionError("예외가 전파돼야 한다")
    except RuntimeError as e:
        assert "poll 실패" in str(e)
    assert conns and all(c.closed for c in conns)

    # run_job 을 거치면 error 행으로 기록되고 예외는 삼켜진다
    db = connect(tmp_path / "t.db")
    init_schema(db, all_migrations(discover()))
    deps2 = DaemonDeps(**{**deps.__dict__, "conn_factory": factory})
    sched = SimpleNamespace(telegram_poll=SimpleNamespace(interval_s=30), ledger_backup="17:10")
    (job,) = build(
        [Feature(id="kernel_builtin", tier="service", jobs=(bj.TELEGRAM_POLL,))], schedule=sched,
        settings=deps2.settings, markets=[], ctx=deps2,
    )  # fmt: skip
    run_job(db, job, deps2, stop_event=threading.Event())
    row = db.execute("SELECT status, error FROM runs WHERE kind='telegram_poll'").fetchone()
    assert row["status"] == "error" and "poll 실패" in row["error"]


def test_daemon_fills_features_when_empty(tmp_path):
    from trader.kernel.daemon.runner import Daemon

    feats = [SimpleNamespace(id="f", jobs=(), queue_kinds=())]
    daemon = Daemon(feats, _deps(tmp_path, Notifier([])))
    assert daemon.deps.features == feats
