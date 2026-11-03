"""scalp 텔레그램 문구."""

from trader.features.scalp import messages as M
from trader.features.scalp.messages import SCALP_EXIT


def test_scalp_digest_bundles_entries_and_exits_in_korean():
    items = [
        {"leg": "entry", "label": "삼성전자(005930)", "price": "71500", "reason": "거래량 3.2배"},
        {
            "leg": "exit",
            "label": "삼성전자(005930)",
            "price": "72600",
            "reason": "take_profit",
            "ret": 0.0154,
        },
        {
            "leg": "exit",
            "label": "SK하이닉스(000660)",
            "price": "200000",
            "reason": "judge_exit",
            "ret": -0.004,
        },
    ]
    text = M.scalp_digest(market="KR", items=items, dry_run=True)
    assert text == (
        "[단타] KR 진입 1 · 청산 2 · 수익 합계 +1.14% (주문 없이 판정)\n"
        "진입 삼성전자(005930) @71,500원 · 거래량 3.2배\n"
        "청산 삼성전자(005930) @72,600원 · 익절 · +1.54%\n"
        "청산 SK하이닉스(000660) @200,000원 · 판정자 조기 청산 · -0.40%"
    )
    assert "dry-run" not in text and "take_profit" not in text
    assert M.scalp(leg="halt", market="KR", label="", reason="실현 -1.20% ≤ -1.00%") == (
        "[단타 중단] KR\n실현 -1.20% ≤ -1.00%\n오늘은 새로 진입하지 않아요."
    )


def test_scalp_exit_has_carry_over_label():
    assert SCALP_EXIT["carry_over"] == "이월 청산"
