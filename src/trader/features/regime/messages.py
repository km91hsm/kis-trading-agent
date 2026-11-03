"""regime 사용자 대면 문구(국면·시장 뷰·킬스위치·권리 일정). 공용 틀·표는 커널 `messages`."""

from __future__ import annotations

from typing import Any

from trader.features.regime.blackout import LABEL
from trader.kernel.notify.messages import REGIME, STANCE, event, first_sentences, render


def regime_change(*, market: str, before: str | None, after: str, count: str) -> str:
    def name(s: str | None) -> str:
        return REGIME.get(s, s) if s else "—"

    return render("국면 전환", f"{market} {name(before)} → {name(after)} ({count})")


def market_view(
    *,
    market: str,
    stance: str | None,
    hedge_weight: Any,
    leveraged: bool,
    rationale: str | None = None,
) -> str:
    stance_text = STANCE.get(str(stance), stance or "—")
    head = f"{market} {stance_text} · 헤지 제안 {float(hedge_weight or 0):.0%}"
    why = first_sentences(rationale)
    return render(
        "시장 뷰", head + ("" if leveraged else " (레버리지 거부)"), [f"근거: {why}" if why else ""]
    )


def killswitch_on(reason: str) -> str:
    """킬스위치 켜짐: 신규 매수 중단(매도·손절·헤지는 계속), 해제는 사람만."""
    return event(
        "킬스위치",
        "신규 매수를 멈췄어요",
        f"사유: {reason}",
        '매도·손절·헤지는 계속돼요. 해제: trader regime killswitch off --note "원인"',
    )


def ksd_stale(items: dict[str, int | None]) -> str:
    """권리 일정(예탁원) 종류별 수집이 STALE_DAYS 넘게 실패(2-O9) — 지난 일정으로 판정 중.
    `items` 는 `blackout.stale_kinds` 결과({종류: 지난 일수 또는 None}), 종류는 한국어로 보인다."""
    lines = [
        f"{LABEL.get(k, k)}: {days}일째 실패" if days else f"{LABEL.get(k, k)}: 수집 기록 없음"
        for k, days in sorted(items.items())
    ]
    tail = "지난 일정으로 블랙아웃을 판정하고 있어요"
    return render("권리 일정 수집 실패", "KR", [*lines, tail])
