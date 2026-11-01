# tests/kernel/notify/test_commands.py
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
    assert "등록된 상태 절 없음" in sent.msgs[-1]
