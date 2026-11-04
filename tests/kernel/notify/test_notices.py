import httpx
import respx

from trader.kernel import notify
from trader.kernel.config.secrets import Secrets
from trader.kernel.ledger import db
from trader.kernel.notify import Message
from trader.kernel.notify.record import ledger_recorder
from trader.kernel.notify.telegram import make_notifier
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations

API = "https://api.telegram.org/botT/sendMessage"
SECRETS = Secrets(TELEGRAM_BOT_TOKEN="T", TELEGRAM_CHAT_ID="1")


@respx.mock
def test_notifier_records_every_send(project):
    respx.post(API).mock(return_value=httpx.Response(200, json={"ok": True}))
    seen: list[dict] = []
    n = make_notifier(SECRETS, record=seen.append)
    assert n.send_message(Message("체결", kind="fill", account_id="paper-main")) is True
    assert len(seen) == 1
    assert seen[0]["kind"] == "fill" and seen[0]["account_id"] == "paper-main"
    assert seen[0]["ok"] is True and seen[0]["channel"] == "telegram" and seen[0]["text"] == "체결"
    assert seen[0]["at"]
    n.send("평문")
    assert len(seen) == 2 and seen[1]["kind"] == "info" and seen[1]["account_id"] is None


@respx.mock
def test_notifier_records_http_failure_as_not_ok(project):
    respx.post(API).mock(return_value=httpx.Response(500))
    seen: list[dict] = []
    n = make_notifier(SECRETS, record=seen.append)
    n.send("x")
    assert len(seen) == 1 and seen[0]["ok"] is False


@respx.mock
def test_ledger_recorder_inserts_and_swallows_errors(project):
    respx.post(API).mock(return_value=httpx.Response(200, json={"ok": True}))
    c = db.connect()
    db.init_schema(c, all_migrations(discover()))
    c.close()
    n = make_notifier(SECRETS, record=ledger_recorder(db.connect))
    n.send_message(Message("안녕", kind="fill", account_id="a"))
    c = db.connect()
    row = c.execute("SELECT * FROM notices").fetchone()
    c.close()
    assert (row["kind"], row["account_id"], row["text"], row["ok"]) == ("fill", "a", "안녕", 1)

    def broken():
        raise RuntimeError("db down")

    assert make_notifier(SECRETS, record=ledger_recorder(broken)).send("y") is True


def test_notify_send_helper_falls_back_to_plain_send():
    class Fake:
        def __init__(self):
            self.sent = []

        def send(self, text, *, escape=True):
            self.sent.append(text)
            return True

    fake = Fake()
    assert notify.send(fake, Message("x", buttons=[[("승인", "a:1")]])) is True
    assert fake.sent == ["x"]


@respx.mock
def test_http_failure_keeps_send_true_but_records_not_ok(project, tmp_path):
    from trader.kernel.notify.telegram import TelegramNotifier

    respx.post(API).mock(return_value=httpx.Response(500))
    seen: list[dict] = []
    n = TelegramNotifier("T", "1", outbox=tmp_path / "ob.jsonl", record=seen.append)
    assert n.send("보관됨") is True  # outbox 에 보관 → True
    assert len(seen) == 1 and seen[0]["ok"] is False


@respx.mock
def test_flush_outbox_records_late_delivery(project, tmp_path):
    from trader.kernel.notify.telegram import TelegramNotifier

    respx.post(API).mock(return_value=httpx.Response(500))
    seen: list[dict] = []
    n = TelegramNotifier("T", "1", outbox=tmp_path / "ob.jsonl", record=seen.append)
    n.send_message(Message("늦은 체결", kind="fill", account_id="paper-main"))
    assert [r["ok"] for r in seen] == [False]
    n.flush_outbox()  # 여전히 500 → 기록 없음
    assert len(seen) == 1
    respx.post(API).mock(return_value=httpx.Response(200, json={"ok": True}))
    assert n.flush_outbox()["sent"] == 1
    assert [r["ok"] for r in seen] == [False, True]
    assert (seen[1]["kind"], seen[1]["account_id"], seen[1]["text"]) == (
        "fill", "paper-main", "늦은 체결",
    )
