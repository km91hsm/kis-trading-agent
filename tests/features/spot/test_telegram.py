"""spot 텔레그램 `/approve`·`/reject`: 실전 확인어·신뢰 밖 발신자·
paused/모드 변경 시 pending 유지."""

from datetime import UTC, datetime

import pytest

from tests.features.spot.approval_helpers import (
    Sent,
    fake_brokers,
    make_account,
    make_pending,
    make_port,
)
from trader.features.accounts import modes
from trader.features.spot.feature import FEATURE
from trader.features.spot.telegram import TELEGRAM_COMMANDS, handle_approve, handle_reject
from trader.kernel.execution import ledger as xledger
from trader.kernel.notify.commands import CommandContext, poll_once
from trader.kernel.notify.telegram import Update


@pytest.fixture
def accounts():
    return [
        make_account("paper-main", "paper", "approve"),
        make_account("real-main", "real", "approve"),
    ]


@pytest.fixture
def brokers(accounts):
    return fake_brokers(accounts)


def _cctx(conn, settings, accounts, brokers, *, ports=True):
    return CommandContext(
        conn=conn,
        settings=settings,
        state=None,
        now=lambda: datetime.now(UTC),
        notifier=Sent(),
        features=[FEATURE],
        ports={"accounts": make_port(accounts, brokers)} if ports else {},
    )


def _status(conn, aid):
    return xledger.get_approval(conn, aid)["status"]


def test_command_table():
    assert [(c.name, c.untrusted_ok) for c in TELEGRAM_COMMANDS] == [
        ("approve", False),
        ("reject", False),
    ]


def test_usage_when_arg_missing_or_not_numeric(conn, settings, accounts, brokers):
    c = _cctx(conn, settings, accounts, brokers)
    assert "사용법: /approve <승인 id>" in handle_approve(c, None)
    assert "사용법: /approve <승인 id>" in handle_approve(c, "abc")
    assert "사용법: /reject <승인 id>" in handle_reject(c, "")
    assert "사용법: /approve <승인 id>" in handle_approve(c, "²")  # 유니코드 숫자는 id 아님


def test_paper_approve_executes(conn, settings, accounts, brokers):
    aid = make_pending(conn, settings, accounts, brokers)
    c = _cctx(conn, settings, accounts, brokers)
    reply = handle_approve(c, str(aid))
    assert f"#{aid}" in reply and "FAKE-1" in reply
    assert len(brokers["paper-main"].orders) == 1 and _status(conn, aid) == "approved"


def test_real_account_needs_confirm_word(conn, settings, accounts, brokers):
    aid = make_pending(conn, settings, accounts, brokers, account_id="real-main")
    c = _cctx(conn, settings, accounts, brokers)
    reply = handle_approve(c, str(aid))
    assert "실전 계좌" in reply and f"/approve {aid} 실전" in reply
    assert _status(conn, aid) == "pending" and brokers["real-main"].orders == []
    reply = handle_approve(c, f"{aid} 실전")
    assert len(brokers["real-main"].orders) == 1 and _status(conn, aid) == "approved"
    assert "실패" not in reply


def test_reject_and_unknown_id(conn, settings, accounts, brokers):
    aid = make_pending(conn, settings, accounts, brokers)
    c = _cctx(conn, settings, accounts, brokers)
    assert f"#{aid}" in handle_reject(c, str(aid))
    assert _status(conn, aid) == "rejected" and brokers["paper-main"].orders == []
    assert "거부 실패" in handle_reject(c, "999")
    assert "승인 실패" in handle_approve(c, "999")


def test_reject_sends_one_reject_notice(conn, settings, accounts, brokers):
    """텔레그램 거부는 `[거부]` 알림 1줄(kind approval·계좌 id)을 남긴다(T5-R1, M9). 실패한 거부는
    알림이 없다."""
    from dataclasses import replace

    class Msgs(Sent):
        def __init__(self):
            super().__init__()
            self.messages = []

        def send_message(self, msg):
            self.messages.append(msg)
            return self.send(msg.text)

    aid = make_pending(conn, settings, accounts, brokers)
    c = replace(_cctx(conn, settings, accounts, brokers), notifier=Msgs())
    handle_reject(c, str(aid))
    (notice,) = c.notifier.messages
    assert notice.text.startswith("[거부]") and f"#{aid}" in notice.text
    assert (notice.kind, notice.account_id) == ("approval", "paper-main")
    handle_reject(c, str(aid))  # 이미 결정됨 → 거부 실패, 알림 없음
    assert len(c.notifier.messages) == 1


def test_untrusted_sender_rejected(conn, settings, accounts, brokers):
    aid = make_pending(conn, settings, accounts, brokers)
    c = _cctx(conn, settings, accounts, brokers)

    class Rx:
        def poll(self, offset=None):
            return [Update(update_id=3, text=f"/approve {aid}", chat_id="1", trusted=False)]

    poll_once(c, Rx())
    assert any("명령 거부" in m for m in c.notifier.msgs)
    assert _status(conn, aid) == "pending" and brokers["paper-main"].orders == []


def test_poll_dispatches_approve_through_registry(conn, settings, accounts, brokers):
    aid = make_pending(conn, settings, accounts, brokers)
    c = _cctx(conn, settings, accounts, brokers)

    class Rx:
        def poll(self, offset=None):
            return [Update(update_id=3, text=f"/approve {aid}", chat_id="1")]

    poll_once(c, Rx())
    assert _status(conn, aid) == "approved" and any("FAKE-1" in m for m in c.notifier.msgs)


def test_paused_keeps_pending(conn, settings, accounts, brokers):
    aid = make_pending(conn, settings, accounts, brokers)
    modes.set_paused(conn, True)
    c = _cctx(conn, settings, accounts, brokers)
    reply = handle_approve(c, str(aid))
    assert "일시정지" in reply
    assert _status(conn, aid) == "pending" and brokers["paper-main"].orders == []


def test_mode_changed_keeps_pending(conn, settings, accounts, brokers):
    aid = make_pending(conn, settings, accounts, brokers)
    modes.set_account_mode(conn, accounts[0], "off")
    c = _cctx(conn, settings, accounts, brokers)
    reply = handle_approve(c, str(aid))
    assert "approve 가 아님" in reply
    assert _status(conn, aid) == "pending" and brokers["paper-main"].orders == []


def test_missing_accounts_port_fails_closed(conn, settings, accounts, brokers):
    aid = make_pending(conn, settings, accounts, brokers)
    c = _cctx(conn, settings, accounts, brokers, ports=False)
    reply = handle_approve(c, str(aid))
    assert "계좌 포트" in reply
    assert _status(conn, aid) == "pending" and brokers["paper-main"].orders == []
