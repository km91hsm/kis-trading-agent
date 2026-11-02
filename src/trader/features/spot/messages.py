"""spot 사용자 대면 문구 — 종목 표기는 spot 이 소유한다(`labels`). 공용 틀·표는 커널 `messages`."""

from __future__ import annotations

from trader.features.spot import labels
from trader.kernel.notify import messages


def approval_line(a: dict, env: str | None = None) -> str:
    """'#12 real-main(실전) 매수 삼성전자(005930) 10주 @71,500원 · 10:42 만료' (KST)."""
    o = a["order"]
    shown = labels.label(str(o.get("ticker")), o.get("name"))
    market = o.get("market")
    who = a["account_id"] + (f"({messages.ENV[env]})" if env in messages.ENV else "")
    price = f"@{messages.money(o.get('price'), market)}" if o.get("price") else "시장가"
    side = messages.SIDE.get(str(o.get("side")), o.get("side"))
    return (
        f"#{a['id']} {who} {side} {shown} {o.get('qty')}주 {price} · "
        f"{messages.kst_hhmm(a.get('expires_at'))} 만료"
    )
