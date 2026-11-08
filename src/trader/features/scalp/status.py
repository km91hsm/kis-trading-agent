"""`/status` 의 「단타」 절: 켠 계좌 목록(없으면 끔) + 시장별 오늘 보유·청산 수.

옛 `status._scalp_lines` 이식."""

from __future__ import annotations

from typing import Any

from trader.features.scalp import switch
from trader.features.scalp.state import load_state
from trader.kernel.registry.types import StatusSection
from trader.kernel.session.run_store import run_date_for


def render(ctx: Any) -> list[str]:
    try:
        settings = ctx.settings
        port = (getattr(ctx, "ports", None) or {}).get("accounts")
        on = (
            [a.id for a in port.accounts() if switch.stored(ctx.conn, settings, a)[0]]
            if port is not None
            else []
        )
        if not on:
            return ["- 끔 (켜진 계좌 없음)"]
        out = [f"- 켬: {', '.join(on)}"]
        now = ctx.now()
        for m in settings.markets:
            st = load_state(getattr(ctx, "state", None), run_date_for(m, now), m)
            halted = f", 중단: {st['halted']}" if st.get("halted") else ""
            out.append(
                f"- {m}: 보유 {len(st.get('open') or [])}·청산 {len(st.get('closed') or [])}"
                f"{halted}"
            )
        return out
    except Exception as e:  # noqa: BLE001 - 상태 조회는 실패해도 /status 는 계속
        return [f"- 단타 조회 실패: {type(e).__name__}: {e}"]


STATUS_SECTION = StatusSection("단타", render)
