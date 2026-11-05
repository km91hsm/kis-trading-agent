# tests/kernel/notify/test_commands.py
import json
from datetime import UTC, datetime, timedelta

import pytest

from trader.kernel.ledger import control
from trader.kernel.notify import commands
from trader.kernel.notify.telegram import Update, parse_command
from trader.kernel.registry.types import Feature, RegistryError, StatusSection, TelegramCommand


class Sent:
    def __init__(self):
        self.msgs = []

    def send(self, text, *, escape=True):
        self.msgs.append(text)
        return True


class Recv:
    last_update_id = None

    def __init__(self, texts, trusted=True):
        self.texts, self.trusted = texts, trusted

    def poll(self, offset=None):
        return [
            Update(update_id=i + 1, text=t, chat_id="1", trusted=self.trusted)
            for i, t in enumerate(self.texts)
        ]


def _feat(store):
    def pause(ctx, arg):
        store["paused"] = True
        return "[일시정지] 멈췄어요"

    def boom(ctx, arg):
        raise RuntimeError("database is locked")

    return Feature(
        id="demo",
        tier="service",
        telegram_commands=(
            TelegramCommand("pause", False, "/pause", pause),
            TelegramCommand("approve", False, "/approve <id>", boom),
            TelegramCommand("daily", True, "/daily", lambda c, a: "[일일] ok"),
        ),
        status_sections=(StatusSection("계좌", lambda ctx: ["실행 중 · 계좌 모드: p 자동"]),),
    )


def _ctx(conn, sent, store, now=None):
    t = now or (lambda: datetime(2026, 10, 28, 1, 0, tzinfo=UTC))
    return commands.CommandContext(
        conn=conn, settings=None, state=None, now=t, notifier=sent, features=[_feat(store)]
    )


def test_parse_multiword_commands():
    assert parse_command("/mode paper-main approve") == ("mode", "paper-main approve")
    assert parse_command("/watch add 000660") == ("watch", "add 000660")
    assert parse_command("/pause") == ("pause", None) and parse_command("/resume@bot") == (
        "resume",
        None,
    )
    assert parse_command("hello") is None


def test_dispatch_and_builtin_status(conn):
    sent, store = Sent(), {}
    r = commands.poll_once(_ctx(conn, sent, store), Recv(["/pause", "/status"]))
    assert r == {"received": 2, "handled": 2, "failed": 0} and store["paused"]
    assert (
        sent.msgs[0] == "[일시정지] 멈췄어요"
        and sent.msgs[1].startswith("[상태]")
        and "계좌 모드" in sent.msgs[1]
    )


def test_poll_once_advances_offset_past_filtered_updates(conn):
    class FilteredRecv(Recv):
        last_update_id = 50

    sent, store = Sent(), {}
    r = commands.poll_once(_ctx(conn, sent, store), FilteredRecv([]))
    assert r["received"] == 0 and control.get_flag(conn, "telegram_offset") == 51

    class MixedRecv(Recv):
        last_update_id = 1

    commands.poll_once(_ctx(conn, sent, store), MixedRecv(["/status", "/status"]))
    assert control.get_flag(conn, "telegram_offset") == 3


def test_command_failure_does_not_block_others_or_offset(conn):
    sent, store = Sent(), {}
    r = commands.poll_once(_ctx(conn, sent, store), Recv(["/approve 7", "/pause"]))
    assert r["handled"] == 2 and r["failed"] == 1 and store["paused"]
    assert control.get_flag(conn, "telegram_offset") == 3
    assert any("[명령 실패]" in m and "/approve 7" in m for m in sent.msgs)


def test_unknown_command_help_at_most_once_per_minute(conn):
    sent, store = Sent(), {}
    t = [datetime(2026, 10, 28, 1, 0, tzinfo=UTC)]
    ctx = _ctx(conn, sent, store, now=lambda: t[0])
    commands.poll_once(ctx, Recv(["/aprove 1", "/foo"]))
    assert (
        sum(m.startswith("[명령 안내]") for m in sent.msgs) == 1
        and "/status" in sent.msgs[-1]
        and "/pause" in sent.msgs[-1]
    )
    t[0] += timedelta(seconds=61)
    commands.poll_once(ctx, Recv(["/bar"]))
    assert sum(m.startswith("[명령 안내]") for m in sent.msgs) == 2
    assert control.get_flag(conn, "telegram_offset") == 2


def test_untrusted_sender_can_only_read(conn):
    sent, store = Sent(), {}
    ctx = _ctx(conn, sent, store)
    commands.poll_once(ctx, Recv(["/pause"], trusted=False))
    assert sent.msgs[-1].startswith("[명령 거부]") and "paused" not in store
    commands.poll_once(ctx, Recv(["/status", "/daily"], trusted=False))
    assert not any(m.startswith("[명령 거부]") for m in sent.msgs[-2:])


def test_duplicate_command_names_rejected():
    f = Feature(
        id="a", tier="service", telegram_commands=(TelegramCommand("x", True, "", lambda c, a: ""),)
    )
    g = Feature(
        id="b", tier="service", telegram_commands=(TelegramCommand("x", True, "", lambda c, a: ""),)
    )
    with pytest.raises(RegistryError, match="x"):
        commands.registry([f, g])
    with pytest.raises(RegistryError, match="status"):
        commands.registry(
            [
                Feature(
                    id="c",
                    tier="service",
                    telegram_commands=(TelegramCommand("status", True, "", lambda c, a: ""),),
                )
            ]
        )


def test_status_without_sections(conn):
    sent = Sent()
    ctx = commands.CommandContext(
        conn=conn,
        settings=None,
        state=None,
        now=lambda: datetime.now(UTC),
        notifier=sent,
        features=[],
    )
    commands.poll_once(ctx, Recv(["/status"]))
    # 기능 절이 없어도 커널 절(운영 점검·데몬)은 늘 있다
    assert "데몬:" in sent.msgs[-1] and "점검 기록 없음" in sent.msgs[-1]


def test_telegram_run_enqueues_when_daemon_alive(project):
    from trader.kernel.daemon import heartbeat
    from trader.kernel.daemon.lock import Lock
    from trader.kernel.ledger import db
    from trader.kernel.platform import paths
    from trader.kernel.registry import discover
    from trader.kernel.registry.contracts import all_migrations

    conn = db.connect()
    db.init_schema(conn, all_migrations(discover()))
    sent = Sent()
    ctx = commands.CommandContext(
        conn=conn, settings=None, state=None,
        now=lambda: datetime(2026, 10, 28, 1, 0, tzinfo=UTC),
        notifier=sent, features=discover(),
    )  # fmt: skip

    def run(text):
        commands.poll_once(ctx, Recv([text]))
        return sent.msgs[-1]

    usage = run("/run")
    assert "사용법" in usage and "fills_sync" in usage
    assert "데몬이 꺼져" in run("/run reconcile KR")
    assert conn.execute("SELECT COUNT(*) FROM job_requests").fetchone()[0] == 0
    lock = Lock(paths.state_dir() / heartbeat.LOCK_NAME)
    assert lock.acquire()
    try:
        first = run("/run reconcile KR")
        assert first.startswith("[요청 #") and "대사" in first
        assert "이미 대기" in run("/run reconcile KR")
        assert "알 수 없는 작업" in run("/run nope")
        assert "파라미터 오류" in run("/run reconcile 005930")
    finally:
        lock.release()
    assert conn.execute("SELECT COUNT(*) FROM job_requests").fetchone()[0] == 1
    # 조작 이력(스펙 §5): 받아들인 요청 1건만 `job:<kind>` 로, channel telegram
    logs = conn.execute("SELECT channel, actor, key, old, new FROM control_log").fetchall()
    assert [(r[0], r[1], r[2], r[3]) for r in logs] == [("telegram", "1", "job:reconcile", None)]
    rid = conn.execute("SELECT id FROM job_requests").fetchone()[0]
    assert json.loads(logs[0][4]) == {"request_id": rid, "params": {"market": "KR"}}


def test_run_untrusted_refused_and_not_registrable(conn):
    sent = Sent()
    ctx = _ctx(conn, sent, {})
    commands.poll_once(ctx, Recv(["/run reconcile KR"], trusted=False))
    assert sent.msgs[-1].startswith("[명령 거부]")
    cmd = TelegramCommand("run", True, "", lambda c, a: "")
    bad = Feature(id="x", tier="service", telegram_commands=(cmd,))
    with pytest.raises(RegistryError):
        commands.registry([bad])


def test_status_message_isolates_failing_section(conn):
    feat = Feature(
        id="demo",
        tier="service",
        status_sections=(
            StatusSection("A", lambda c: ["- a"]),
            StatusSection("B", lambda c: 1 / 0),
            StatusSection("C", lambda c: ["- c"]),
        ),
    )
    ctx = commands.CommandContext(
        conn=conn,
        settings=None,
        state=None,
        now=lambda: datetime(2026, 10, 28, 1, 0, tzinfo=UTC),
        notifier=Sent(),
        features=[feat],
    )
    text = commands.status_message(ctx)
    assert "- a" in text and "- c" in text
    assert "- B: 조회 실패(ZeroDivisionError" in text
