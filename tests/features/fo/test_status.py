"""파생 상태 줄·`/fo` 명령·「파생(선물옵션)」 절(옛 `test_daemon_fo` 의 텔레그램·STATUS 건)."""

from __future__ import annotations

from types import SimpleNamespace

from tests.features.fo._support import ACCT, _held_position, at, make_ctx
from trader.features.fo import status
from trader.features.fo.feature import FEATURE
from trader.features.fo.telegram import FO_CMD
from trader.kernel.ledger import control
from trader.kernel.notify import commands
from trader.kernel.notify.telegram import Update, parse_command
from trader.kernel.registry import discover


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
        return [Update(update_id=i + 1, text=t, chat_id="1", trusted=self.trusted)
                for i, t in enumerate(self.texts)]  # fmt: skip


def _cmd_ctx(conn, sent, now):
    return commands.CommandContext(
        conn=conn, settings=None, state=None, now=lambda: now, notifier=sent,
        features=discover(),
    )  # fmt: skip


def test_empty_ledger_lines(conn):
    assert status.lines(conn, at(10)) == ["- 보유 없음", "- 오늘 결정: 없음"]


def test_held_position_line(conn):
    _held_position(conn, make_ctx(conn, now=at(10)), qty=2)
    out = status.lines(conn, at(10))
    assert "105W11" in out[0] and "2계약" in out[0] and "[보유]" in out[0]
    assert "지수 손절 345" in out[0]


def test_unknown_and_paused_lines(conn):
    _held_position(conn, make_ctx(conn, now=at(10)), qty=1, status="unknown")
    control.set_flag(conn, "paused", True)
    out = status.lines(conn, at(10))
    assert "- 사람 확인 필요: unknown·청산 실패 1건(`trader fo adopt|close`)" in out
    assert out[-1] == "- 일시정지 중 — 새 진입·재평가는 기록만(손절·청산·축소는 계속)"


def test_halted_flag_line(conn):
    control.set_flag(conn, f"fo_halted:{ACCT}", "2026-10-27")
    assert f"- {ACCT}: 일일 손실 한도 — 오늘 신규 금지" in status.lines(conn, at(10))


def test_status_section_declared_and_isolated(conn):
    sec = FEATURE.status_sections[0]
    assert sec.title == "파생(선물옵션)" and sec is status.STATUS_SECTION
    _held_position(conn, make_ctx(conn, now=at(10)), qty=2)
    ok = sec.render(SimpleNamespace(conn=conn, now=lambda: at(10)))
    assert "105W11" in ok[0]
    bad = sec.render(SimpleNamespace(conn=None, now=lambda: at(10)))
    assert len(bad) == 1 and "조회 실패" in bad[0]


def test_status_section_omitted_without_fo_account_or_rows(conn):
    """fo 계좌도 fo_positions·fo_decisions 행도 없으면 절 생략(빈 목록). 파생 계좌가 원장
    `accounts` 에 있으면 행이 없어도 절을 보인다(M10 최종 리뷰 M1)."""
    from tests.features.fo._support import account
    from trader.features.accounts import modes

    sec = status.STATUS_SECTION
    ctx = SimpleNamespace(conn=conn, now=lambda: at(10))
    assert sec.render(ctx) == []
    modes.sync_accounts(conn, [account()])  # 파생 계좌를 원장 accounts 에 동기화
    assert sec.render(ctx) == ["- 보유 없음", "- 오늘 결정: 없음"]


def test_telegram_fo_status_line(conn):
    assert parse_command("/fo") == ("fo", None)
    _held_position(conn, make_ctx(conn, now=at(10)), qty=2)
    sent = Sent()
    r = commands.poll_once(_cmd_ctx(conn, sent, at(10)), Recv(["/fo"]))
    assert r["handled"] == 1 and r["failed"] == 0
    assert sent.msgs[0].startswith("[파생]") and "105W11" in sent.msgs[0]
    assert "2계약" in sent.msgs[0]


def test_fo_command_registered_and_untrusted_ok(conn):
    assert FEATURE.telegram_commands == (FO_CMD,)
    assert FO_CMD.untrusted_ok is True and "/fo" in commands.help_line(discover())
    sent = Sent()
    commands.poll_once(_cmd_ctx(conn, sent, at(10)), Recv(["/fo"], trusted=False))
    assert not any(m.startswith("[명령 거부]") for m in sent.msgs)


def test_status_message_includes_fo_section(conn):
    _held_position(conn, make_ctx(conn, now=at(10)), qty=2)
    text = commands.status_message(_cmd_ctx(conn, Sent(), at(10)))
    assert "105W11" in text
