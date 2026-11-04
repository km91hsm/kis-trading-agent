"""spot 승인 알림 버튼·텔레그램 콜백 `apv:*`: 모의 1단계, 실전 2단계(`ask` → `yes:<계좌>`),
텍스트 `/approve` 도 `approvals.decide` 를 지난다. 콜백은 데몬 안에서 돌아 바로 집행한다."""

import json
from datetime import UTC, datetime

import pytest

from tests.features.spot.approval_helpers import fake_brokers, make_account, make_pending, make_port
from trader.features.spot.feature import FEATURE
from trader.features.spot.telegram import handle_approve, handle_callback
from trader.kernel.execution import ledger as xledger
from trader.kernel.notify.commands import CommandContext, poll_once
from trader.kernel.notify.telegram import Message, Update

BASE = "https://pc.example.ts.net"


class Box:
    """버튼까지 받는 알림기(`send_message`) — 보낸 Message 를 모은다."""

    def __init__(self):
        self.msgs: list[Message] = []

    def send_message(self, msg: Message) -> bool:
        self.msgs.append(msg)
        return True

    def send(self, text, *, escape=True):
        self.msgs.append(Message(text))
        return True

    def texts(self) -> list[str]:
        return [m.text for m in self.msgs]


class Rx:
    """콜백 업데이트를 한 번 내주고 응답·수정을 기록하는 수신기."""

    def __init__(self, *updates):
        self.updates = list(updates)
        self.answers: list[tuple[str, str]] = []
        self.edits: list[tuple] = []

    def poll(self, offset=None):
        out, self.updates = self.updates, []
        return out

    def answer_callback(self, callback_id, text=""):
        self.answers.append((callback_id, text))

    def edit_message(self, chat_id, message_id, text):
        self.edits.append((chat_id, message_id, text))


@pytest.fixture
def accounts():
    return [
        make_account("paper-main", "paper", "approve"),
        make_account("real-main", "real", "approve"),
    ]


@pytest.fixture
def brokers(accounts):
    return fake_brokers(accounts)


@pytest.fixture
def linked(settings):
    return settings.model_copy(
        update={"web": settings.web.model_copy(update={"public_base": BASE})}
    )


def _cctx(conn, settings, accounts, brokers, notifier=None):
    return CommandContext(
        conn=conn,
        settings=settings,
        state=None,
        now=lambda: datetime.now(UTC),
        notifier=notifier or Box(),
        features=[FEATURE],
        ports={"accounts": make_port(accounts, brokers)},
    )


def _press(cctx, data, uid=10):
    rx = Rx(
        Update(
            update_id=uid, text="", chat_id="1", sender_id="77", kind="callback", data=data,
            callback_id=f"cb{uid}", message_id=5, message_text="[승인 요청]",
        )
    )  # fmt: skip
    poll_once(cctx, rx)
    return rx


def _approval(conn, aid):
    return xledger.get_approval(conn, aid)


def _pending_by_account(conn) -> dict[str, int]:
    return {a["account_id"]: a["id"] for a in xledger.pending_approvals(conn)}


def test_approval_notice_has_buttons_by_env(conn, linked, accounts, brokers):
    box = Box()
    make_pending(conn, linked, accounts, brokers, notifier=box)
    ids = _pending_by_account(conn)
    notices = {m.account_id: m for m in box.msgs if m.kind == "approval"}
    paper, real = notices["paper-main"], notices["real-main"]
    p, r = ids["paper-main"], ids["real-main"]
    assert paper.buttons == [[("승인", f"apv:{p}:yes"), ("거절", f"apv:{p}:no")]]
    assert real.buttons == [[("실전 승인…", f"apv:{r}:ask"), ("거절", f"apv:{r}:no")]]
    dec_id = _approval(conn, p)["decision_id"]
    assert paper.text.splitlines()[-1] == f"{BASE}/decisions/spot/{dec_id}"
    assert f"승인 요청 #{p}" in paper.text


def test_approval_notice_without_public_base_has_no_link(conn, settings, accounts, brokers):
    box = Box()
    make_pending(conn, settings, accounts, brokers, notifier=box)
    notices = [m for m in box.msgs if m.kind == "approval"]
    assert notices and all("/decisions/spot/" not in m.text for m in notices)
    assert all(m.buttons for m in notices)


def test_callback_yes_decides_and_executes(conn, settings, accounts, brokers):
    make_pending(conn, settings, accounts, brokers)
    aid = _pending_by_account(conn)["paper-main"]
    c = _cctx(conn, settings, accounts, brokers)
    rx = _press(c, f"apv:{aid}:yes")
    assert len(brokers["paper-main"].orders) == 1
    a = _approval(conn, aid)
    assert (a["status"], a["decided_channel"], a["decided_by"]) == ("approved", "telegram", "77")
    assert "FAKE-1" in rx.answers[0][1] and "FAKE-1" in rx.edits[0][2]
    log = conn.execute("SELECT * FROM control_log").fetchall()
    assert [(x["key"], json.loads(x["new"]), x["channel"], x["actor"]) for x in log] == [
        (f"approval:{aid}", "approve", "telegram", "77")
    ]
    # 같은 버튼을 두 번 눌러도(커널은 중복을 막지 않는다) 다시 주문하지 않는다
    rx2 = _press(c, f"apv:{aid}:yes", uid=11)
    assert len(brokers["paper-main"].orders) == 1 and "FAKE-1" not in rx2.answers[0][1]
    assert conn.execute("SELECT COUNT(*) FROM control_log").fetchone()[0] == 1


def test_callback_ask_then_confirm_for_real(conn, settings, accounts, brokers):
    make_pending(conn, settings, accounts, brokers, account_id="real-main")
    aid = _pending_by_account(conn)["real-main"]
    box = Box()
    c = _cctx(conn, settings, accounts, brokers, notifier=box)
    _press(c, f"apv:{aid}:ask")
    assert brokers["real-main"].orders == []
    confirms = [m for m in box.msgs if m.buttons]
    assert len(confirms) == 1
    assert confirms[0].buttons == [[("실전 real-main 승인 확인", f"apv:{aid}:yes:real-main")]]
    a = _approval(conn, aid)
    assert (a["status"], a["decided_by"]) == ("pending", None)
    # 계좌 확인 없이 yes 를 보내면(손으로 만든 데이터) 실전은 거부
    _press(c, f"apv:{aid}:yes", uid=11)
    _press(c, f"apv:{aid}:yes:paper-main", uid=12)
    assert brokers["real-main"].orders == [] and _approval(conn, aid)["decided_by"] is None
    _press(c, f"apv:{aid}:yes:real-main", uid=13)
    assert len(brokers["real-main"].orders) == 1 and _approval(conn, aid)["status"] == "approved"


def test_callback_no_rejects(conn, settings, accounts, brokers):
    make_pending(conn, settings, accounts, brokers, account_id="real-main")
    aid = _pending_by_account(conn)["real-main"]
    c = _cctx(conn, settings, accounts, brokers)
    rx = _press(c, f"apv:{aid}:no")
    a = _approval(conn, aid)
    assert (a["status"], a["decided_channel"]) == ("rejected", "telegram")
    assert brokers["real-main"].orders == [] and f"#{aid}" in rx.answers[0][1]


def test_callback_bad_data_is_refused(conn, settings, accounts, brokers):
    c = _cctx(conn, settings, accounts, brokers)
    for data in ("apv:x:yes", "apv:1", "apv:1:maybe", "apv:²:yes"):
        assert "알 수 없는" in handle_callback(c, data)


def test_text_approve_still_works_via_decide(conn, settings, accounts, brokers):
    make_pending(conn, settings, accounts, brokers)
    ids = _pending_by_account(conn)
    c = _cctx(conn, settings, accounts, brokers)
    reply = handle_approve(c, str(ids["paper-main"]))
    assert "FAKE-1" in reply
    a = _approval(conn, ids["paper-main"])
    assert (a["status"], a["decided_channel"]) == ("approved", "telegram")
    # 실전은 확인어 없이는 결정조차 하지 않는다
    reply = handle_approve(c, str(ids["real-main"]))
    assert "실전 계좌" in reply and _approval(conn, ids["real-main"])["decided_by"] is None
    reply = handle_approve(c, f"{ids['real-main']} 실전")
    assert len(brokers["real-main"].orders) == 1
    assert _approval(conn, ids["real-main"])["decided_channel"] == "telegram"
    # 이미 결정된 것은 다시 실행하지 않는다
    assert "실패" in handle_approve(c, str(ids["paper-main"]))
    assert len(brokers["paper-main"].orders) == 1
