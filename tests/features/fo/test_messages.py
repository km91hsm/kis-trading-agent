"""파생 알림 문구(옛 notify/messages.py:585-805 이식)."""

from trader.features.fo import messages as m
from trader.kernel.notify.messages import DRY_RUN


def _order(**kw):
    base = dict(account="paper-fo", code="A05W11", name=None, intent="OPEN", side="BUY", qty=2,
                order_type="LIMIT", price="350.5", status="accepted", order_no="0001")  # fmt: skip
    return m.fo_order(**(base | kw))


def test_fo_order_title_and_lines():
    text = _order(leverage=1.234, stop_pt="345", direction="LONG", vehicle="OPTION_SELL",
                  kind="PUT")  # fmt: skip
    assert text.splitlines()[0] == "[파생 주문] paper-fo · A05W11"
    assert "롱(상승) · 풋 매도" in text
    assert "진입 · 매수 2계약 · 350.5pt 지정가" in text
    assert "상태 accepted · 접수번호 0001" in text
    assert "배수 1.23" in text and "지수 손절선 345" in text


def test_fo_order_dry_run_and_market():
    text = _order(dry_run=True, order_type="MARKET", order_no=None)
    assert text.startswith(f"[파생 주문 · {DRY_RUN}]")
    assert "시장가" in text and "접수번호" not in text


def test_fo_what():
    assert m.fo_what(None, "FUTURES", None) == ""
    assert m.fo_what("SHORT", "FUTURES", None) == "숏(하락) · 선물"
    assert m.fo_what("LONG", "OPTION_BUY", "CALL") == "롱(상승) · 콜 매수"
    assert m.fo_what("LONG", "OPTION_BUY", None) == "롱(상승) · 옵션 매수"


def test_fo_reject_caps_reasons_at_five():
    text = m.fo_reject(account="a", code="c", name="이름", intent="RESIZE_UP",
                       reasons=[f"r{i}" for i in range(8)])  # fmt: skip
    lines = text.splitlines()
    assert lines[0] == "[파생 판정 거부] a · 이름(c)"
    assert lines[1] == "증액 거부" and lines[2:] == [f"r{i}" for i in range(5)]


def test_fo_halted_text():
    text = m.fo_halted(account="a", loss="-3500000", limit="3000000")
    assert text.startswith("[파생 일일 손실 한도] a")
    assert "오늘 손익 -3,500,000원 ≤ 한도 −3,000,000원" in text
    assert "보유분 감시·청산은 계속해요." in text


def test_fo_settlement_with_and_without_pnl():
    assert "만기 정산 손익 12,000원" in m.fo_settlement(account="a", code="c", name=None, pnl=12000)
    assert "HTS 체결·잔고로 확인하세요" in m.fo_settlement(
        account="a", code="c", name=None, pnl=None
    )


def test_misc_messages_render():
    assert "다시 내지 않아요" in m.fo_unknown_order(
        account="a", code="c", name=None, side="SELL", qty=1, err="timeout"
    )
    assert "3회 재시도" in m.fo_exit_failed(account="a", code="c", name=None, qty=2, attempts=3)
    assert "매도 2계약" in m.fo_unknown_holding(account="a", code="c", side="SHORT", qty=2)
    assert "미확정 행: x" in m.fo_field_unverified(account="a", items=["x"])
    assert "취소 요청·확정" in m.fo_recall(
        account="a", code="c", name=None, reason="창 밖", cancelled=1
    )
    assert "누적 체결 3계약 > 다리 수량 2계약" in m.fo_fill_anomaly(
        account="a", code="c", name=None, leg_qty=2, filled=3, order_no="9"
    )
    assert "브로커 1계약" in m.fo_position_mismatch(
        account="a", code="c", name=None, ledger_qty=2, broker_qty=1
    )
    assert "잔고에 없음" in m.fo_position_mismatch(
        account="a", code="c", name=None, ledger_qty=2, broker_qty=None
    )
    assert m.fo_exit(account="a", code="c", name=None, intent="STOP", reason="r", qty=1).startswith(
        "[파생 손절]"
    )
    assert "3계약 → 1계약" in m.fo_risk_reduce(
        account="a", code="c", name=None, reason="r", held=3, target=1
    )
    text = m.fo_maintain_warning(account="a", reasons=[f"r{i}" for i in range(6)])
    assert "r3" in text and "r4" not in text and "15:00 확정 검사" in text


def test_constants():
    assert set(m.FO_INTENT) == {
        "OPEN", "RESIZE_UP", "RESIZE_DOWN", "CLOSE", "STOP", "EXPIRY", "SESSION_CLOSE",
        "RISK_REDUCE",
    }  # fmt: skip
    assert m.VEHICLE["OPTION_SELL"] == "옵션 매도" and m.DIRECTION["FLAT"] == "관망"
    assert m.CONTRACT_SIDE == {"LONG": "매수", "SHORT": "매도"}
    assert m.FO_KIND == {"FUTURES": "선물", "CALL": "콜", "PUT": "풋"}
    assert m.fo_label("c") == "c" and m.fo_label("c", "n") == "n(c)"
