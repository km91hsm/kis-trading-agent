"""scalp 텔레그램 문구."""

from __future__ import annotations

from typing import Any

from trader.kernel.notify.messages import DRY_RUN, money, render

SCALP_EXIT = {
    "take_profit": "익절",
    "stop": "손절",
    "close": "마감 청산",
    "max_hold": "보유 시간 초과",
    "judge_exit": "판정자 조기 청산",
    "carry_over": "이월 청산",
}


def scalp(
    *, leg: str, market: str, label: str, price: Any = None, reason: str = "", ret: Any = None
) -> str:
    """leg: entry|exit|halt. 진입·청산은 틱마다 보내지 않고 `scalp_digest` 로 묶는다."""
    if leg == "halt":
        return render("단타 중단", market, [reason, "오늘은 새로 진입하지 않아요."])
    lines = [f"@{money(price, market)}" + (f" · 수익 {float(ret):+.2%}" if ret is not None else "")]
    lines.append(SCALP_EXIT.get(reason, reason))
    return render("단타 진입" if leg == "entry" else "단타 청산", f"{market} {label}", lines)


def scalp_digest(*, market: str, items: list[dict], dry_run: bool = False) -> str:
    """단타 진입·청산 묶음 요약(틱마다 오던 알림 피로). items 는 엔진 outbox 의
    {leg, label, price, reason, ret?} 순서 그대로. 수익 합계는 이 묶음 청산 수익률의 단순 합."""
    entries = [i for i in items if i.get("leg") == "entry"]
    exits = [i for i in items if i.get("leg") == "exit"]
    head = f"{market} 진입 {len(entries)} · 청산 {len(exits)}"
    if exits:
        head += f" · 수익 합계 {sum(float(i.get('ret') or 0) for i in exits):+.2%}"
    if dry_run:
        head += f" ({DRY_RUN})"
    lines = []
    for i in items:
        where = f"{i.get('label')} @{money(i.get('price'), market)}"
        if i.get("leg") == "entry":
            lines.append(f"진입 {where} · {i.get('reason') or '—'}")
        else:
            why = SCALP_EXIT.get(str(i.get("reason")), str(i.get("reason") or "—"))
            ret = f" · {float(i['ret']):+.2%}" if i.get("ret") is not None else ""
            lines.append(f"청산 {where} · {why}{ret}")
    return render("단타", head, lines)
