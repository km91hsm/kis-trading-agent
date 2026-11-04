"""장중 모니터의 순수 판정 — 구조화 무효화 조건 하나(L16). spot 모니터와 fo 트리거가 함께 쓴다."""

from __future__ import annotations

from decimal import Decimal

_OPS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


def rule_hit(rule: dict, quote: Decimal, closes: list[Decimal | None] | None) -> bool | None:
    """구조화 무효화 조건 하나(L16). True/False, 판정할 수 없으면(종가 부족·빠진 거래일) None.
    `closes` 는 거래일 순서대로 정렬된 완성 일봉 종가(오래된 → 최근, 오늘 장중 봉 제외, 그 종목만
    빠진 날은 None — PR #63 Codex: 빠진 날을 지우면 연속이 아닌 종가가 연속처럼 보인다)."""
    op = _OPS.get(str(rule.get("op")))
    try:
        value = Decimal(str(rule.get("value")))
    except (ArithmeticError, ValueError):
        return None
    if op is None or value <= 0:
        return None
    if rule.get("metric") == "price":
        return op(quote, value)
    if rule.get("metric") == "close":
        days = int(rule.get("days") or 1)
        if not closes or len(closes) < days:
            return None
        window = closes[-days:]
        if any(c is None for c in window):
            return None
        return all(op(c, value) for c in window)
    return None
