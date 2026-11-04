# tests/kernel/notify/test_telegram_buttons.py
"""인라인 버튼 발신·callback_query 수신·기능 핸들러 라우팅 (M11 Task 4)."""

import json
from datetime import UTC, datetime

import httpx
import pytest

from trader.kernel.ledger import control
from trader.kernel.notify import commands
from trader.kernel.notify.telegram import Message, TelegramNotifier, TelegramReceiver, Update
from trader.kernel.registry.types import Feature, TelegramCallback

BUTTONS = [[("승인", "apv:1:yes"), ("거부", "apv:1:no")]]


class FakeHttp:
    """httpx.MockTransport 로 요청을 기록하는 가짜 HTTP. 응답은 메서드 이름(경로 끝)별로 정한다."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []  # (HTTP 메서드, 봇 API 메서드, 페이로드)
        self.status = 200
        self.bodies: dict[str, dict] = {}
        self.client = httpx.Client(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        api = request.url.path.rsplit("/", 1)[-1]
        if request.method == "GET":
            payload = dict(request.url.params)
        else:
            payload = dict(httpx.QueryParams(request.read().decode("utf-8")))
            if "reply_markup" in payload:
                payload["reply_markup"] = json.loads(payload["reply_markup"])
        self.calls.append((request.method, api, payload))
        return httpx.Response(self.status, json=self.bodies.get(api, {"ok": True}))

    def posts(self, api: str) -> list[dict]:
        return [p for m, a, p in self.calls if m == "POST" and a == api]


@pytest.fixture
def fake_http() -> FakeHttp:
    return FakeHttp()


class FakeReceiver:
    """poll 결과를 정해 두고 answer_callback·edit_message 호출을 기록한다."""

    last_update_id = None

    def __init__(self):
        self.updates: list[Update] = []
        self.answers: list[tuple[str, str]] = []
        self.edits: list[tuple[str, int, str]] = []

    def poll(self, offset=None):
        return list(self.updates)

    def answer_callback(self, callback_id, text):
        self.answers.append((callback_id, text))
        return True

    def edit_message(self, chat_id, message_id, text):
        self.edits.append((chat_id, message_id, text))
        return True


@pytest.fixture
def fake_receiver() -> FakeReceiver:
    return FakeReceiver()


class Sent:
    def __init__(self):
        self.msgs = []

    def send(self, text, *, escape=True):
        self.msgs.append(text)
        return True


def _ctx(conn, features):
    return commands.CommandContext(
        conn=conn,
        settings=None,
        state=None,
        now=lambda: datetime(2026, 11, 4, 1, 0, tzinfo=UTC),
        notifier=Sent(),
        features=features,
    )


@pytest.fixture
def seen() -> list:
    return []


@pytest.fixture
def ctx_with_callback_feature(conn, seen):
    def approve(ctx, data):
        seen.append((data, ctx.actor))
        return "승인했어요 #1"

    def boom(ctx, data):
        raise RuntimeError("database is locked")

    feat = Feature(
        id="demo",
        tier="service",
        telegram_callbacks=(TelegramCallback("apv", approve), TelegramCallback("bad", boom)),
    )
    return _ctx(conn, [feat])


def _cb(uid, data, *, trusted=True, sender="7"):
    return Update(
        update_id=uid,
        text="",
        chat_id="123",
        sender_id=sender,
        trusted=trusted,
        kind="callback",
        data=data,
        callback_id=f"c{uid}",
        message_id=70 + uid,
        message_text="[승인 요청] 005930 매수 10주",
    )


def test_send_message_with_buttons_posts_reply_markup(fake_http):
    n = TelegramNotifier("T", "123", http=fake_http.client)
    assert n.send_message(Message("가" * 4500, kind="approval", buttons=BUTTONS)) is True
    posts = fake_http.posts("sendMessage")
    assert len(posts) == 2
    kb = posts[0]["reply_markup"]["inline_keyboard"]
    assert kb[0][0] == {"text": "승인", "callback_data": "apv:1:yes"}
    assert kb[0][1] == {"text": "거부", "callback_data": "apv:1:no"}
    assert "reply_markup" not in posts[1]  # 버튼은 첫 청크에만
    # 버튼 없는 평문 send 는 reply_markup 을 붙이지 않는다
    n.send("평문")
    assert "reply_markup" not in fake_http.posts("sendMessage")[-1]


def test_outbox_resend_keeps_buttons_and_records_once(fake_http, tmp_path):
    rows: list[dict] = []
    fake_http.status = 500
    n = TelegramNotifier(
        "T", "123", http=fake_http.client, outbox=tmp_path / "ob.jsonl", record=rows.append
    )
    assert n.send_message(Message("가" * 4500, kind="approval", buttons=BUTTONS)) is True
    assert [r["ok"] for r in rows] == [False]
    fake_http.calls.clear()
    fake_http.status = 200
    assert n.flush_outbox()["sent"] == 2
    posts = fake_http.posts("sendMessage")
    assert posts[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "apv:1:yes"
    assert "reply_markup" not in posts[1]
    assert [r["ok"] for r in rows] == [False, True]  # 늦은 전달도 메시지당 1건


def test_poll_converts_callback_query(fake_http):
    fake_http.bodies["getUpdates"] = {
        "ok": True,
        "result": [
            {
                "update_id": 5,
                "callback_query": {
                    "id": "c1",
                    "from": {"id": 7},
                    "data": "apv:1:yes",
                    "message": {
                        "message_id": 7,
                        "chat": {"id": 123, "type": "private"},
                        "text": "[승인 요청] 005930",
                    },
                },
            },
            {  # 다른 채팅의 콜백은 버린다
                "update_id": 6,
                "callback_query": {
                    "id": "c2",
                    "from": {"id": 7},
                    "data": "apv:2:yes",
                    "message": {"message_id": 8, "chat": {"id": 999, "type": "private"}},
                },
            },
        ],
    }
    r = TelegramReceiver("T", "123", http=fake_http.client)
    ups = r.poll(offset=3)
    assert "callback_query" in json.loads(fake_http.calls[0][2]["allowed_updates"])
    assert ups == [
        Update(
            update_id=5,
            text="",
            chat_id="123",
            sender_id="7",
            trusted=True,
            kind="callback",
            data="apv:1:yes",
            callback_id="c1",
            message_id=7,
            message_text="[승인 요청] 005930",
        )
    ]
    assert r.last_update_id == 6
    # 그룹 채팅 콜백: 허용 목록 판정은 메시지와 같다
    fake_http.bodies["getUpdates"]["result"][0]["callback_query"]["message"]["chat"]["type"] = (
        "group"
    )
    assert r.poll()[0].trusted is False
    allowed = TelegramReceiver("T", "123", http=fake_http.client, allowed_users={"7"})
    assert allowed.poll()[0].trusted is True


def test_answer_callback_and_edit_message_post_to_bot_api(fake_http):
    r = TelegramReceiver("T", "123", http=fake_http.client)
    assert r.answer_callback("c1", "승인했어요") is True
    assert r.edit_message("123", 7, "원문\n\n승인했어요") is True
    assert fake_http.posts("answerCallbackQuery") == [
        {"callback_query_id": "c1", "text": "승인했어요"}
    ]
    assert fake_http.posts("editMessageText") == [
        {"chat_id": "123", "message_id": "7", "text": "원문\n\n승인했어요"}
    ]
    fake_http.status = 400
    assert r.answer_callback("c1", "x") is False


def test_poll_once_routes_callback_to_feature_handler_and_answers(
    ctx_with_callback_feature, fake_receiver, seen
):
    ctx = ctx_with_callback_feature
    fake_receiver.updates = [_cb(9, "apv:1:yes")]
    r = commands.poll_once(ctx, fake_receiver)
    assert seen == [("apv:1:yes", "7")]
    assert fake_receiver.answers == [("c9", "승인했어요 #1")]
    assert fake_receiver.edits == [("123", 79, "[승인 요청] 005930 매수 10주\n\n승인했어요 #1")]
    assert control.get_flag(ctx.conn, commands.OFFSET_FLAG) == 10
    assert r == {"received": 1, "handled": 1, "failed": 0}
    assert ctx.notifier.msgs == []


def test_callback_without_handler_is_answered_not_crashed(
    ctx_with_callback_feature, fake_receiver, seen
):
    ctx = ctx_with_callback_feature
    fake_receiver.updates = [_cb(1, "zzz:1"), _cb(2, "bad:1"), _cb(3, "apv:2:no")]
    r = commands.poll_once(ctx, fake_receiver)
    assert fake_receiver.answers[0] == ("c1", "처리할 수 없는 버튼")
    cid, text = fake_receiver.answers[1]
    assert cid == "c2" and "실패" in text and len(text) <= 200
    assert fake_receiver.answers[2] == ("c3", "승인했어요 #1")  # 앞 실패가 뒤를 막지 않는다
    assert seen == [("apv:2:no", "7")]
    assert [e[1] for e in fake_receiver.edits] == [73]
    assert control.get_flag(ctx.conn, commands.OFFSET_FLAG) == 4
    assert r["failed"] == 1


def test_untrusted_callback_rejected(ctx_with_callback_feature, fake_receiver, seen):
    ctx = ctx_with_callback_feature
    fake_receiver.updates = [_cb(4, "apv:1:yes", trusted=False)]
    commands.poll_once(ctx, fake_receiver)
    assert seen == []
    assert fake_receiver.edits == []
    ((cid, text),) = fake_receiver.answers
    assert cid == "c4" and "TELEGRAM_ALLOWED_USERS" in text
    assert control.get_flag(ctx.conn, commands.OFFSET_FLAG) == 5
