from decimal import Decimal

from trader.kernel.notify import messages as M


def test_money_and_sentences():
    assert M.money(Decimal("71500"), "KR") == "71,500원"
    assert M.money("227.5", "US") == "$227.50"
    assert M.money(None, "KR") == "—"
    assert M.first_sentences("가. 나. 다.") == "가. 나."


def test_pct_and_kst():
    assert M.pct(0.1234, 1) == "12.3%" and M.pct("x") == "—"
    assert M.kst_hhmm("2026-10-26T00:30:00+00:00") == "09:30" and M.kst_hhmm(None) == "—"


def test_render_event_failure_mode():
    assert M.event("상태", "실행 중", "", "둘째") == "[상태] 실행 중\n둘째"
    assert (
        M.failure("명령 실패", "/x", RuntimeError("boom"))
        == "[명령 실패] /x\n오류: RuntimeError: boom"
    )
    assert M.mode_changed(account="p", mode="approve") == "[모드] p → 승인"
    assert M.render("태그", "", (), action="/approve 1") == "[태그]\n\n/approve 1"
