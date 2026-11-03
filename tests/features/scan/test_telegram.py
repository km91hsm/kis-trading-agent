"""scan 텔레그램 `/watch` — 옛 `test_telegram_pause_resume_mode_watch` 의 watch 단언 이식."""

from datetime import UTC, datetime

from trader.kernel.notify import commands
from trader.kernel.notify.telegram import Update
from trader.kernel.registry import discover


class Sent:
    def __init__(self):
        self.msgs = []

    def send(self, text, *, escape=True):
        self.msgs.append(text)
        return True


class Recv:
    last_update_id = None

    def __init__(self, texts):
        self.texts = texts

    def poll(self, offset=None):
        return [Update(update_id=i + 1, text=t, chat_id="1") for i, t in enumerate(self.texts)]


def _ctx(conn, sent):
    return commands.CommandContext(
        conn=conn, settings=None, state=None, now=lambda: datetime.now(UTC),
        notifier=sent, features=discover(),
    )  # fmt: skip


def test_telegram_watch_add_remove_list(conn):
    sent = Sent()
    ctx = _ctx(conn, sent)
    commands.poll_once(ctx, Recv(["/watch add 005930"]))
    assert "005930" in sent.msgs[-1]
    commands.poll_once(ctx, Recv(["/watch add 000660", "/watch list"]))
    assert "005930" in sent.msgs[-1] and "000660" in sent.msgs[-1]
    commands.poll_once(ctx, Recv(["/watch remove 005930"]))
    assert "005930" not in sent.msgs[-1] and "000660" in sent.msgs[-1]
    commands.poll_once(ctx, Recv(["/watch add !!bad"]))
    assert sent.msgs[-1].startswith("거부")
    commands.poll_once(ctx, Recv(["/watch zzz"]))
    assert "사용법" in sent.msgs[-1]
    commands.poll_once(ctx, Recv(["/watch"]))
    assert sent.msgs[-1].startswith("[관심종목]")
