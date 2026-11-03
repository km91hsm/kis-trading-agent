"""briefing 텔레그램 문구."""

from __future__ import annotations

from trader.kernel.notify.messages import event


def briefing(lines: list[str], uncertain: list[str]) -> str:
    """아침 브리핑(사람이 읽는 요약 — 결정·주문과 무관)."""
    return event(
        "아침 브리핑",
        "",
        *[f"· {ln}" for ln in lines],
        *[f"(불확실) {u}" for u in uncertain],
    )
