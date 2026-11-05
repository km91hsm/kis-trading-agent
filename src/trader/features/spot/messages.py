"""spot 사용자 대면 문구 — 종목 표기는 spot 이 소유한다(`labels`). 공용 틀·표는 커널 `messages`."""

from __future__ import annotations

from typing import Any

from trader.features.spot import labels
from trader.kernel.notify import messages
from trader.kernel.notify.telegram import Message


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


def reject_notice(a: dict, env: str | None = None) -> Message:
    """승인 거부 알림 '[거부]' + 승인 줄(kind approval) — 텔레그램 `/reject` 와 웹 거부 큐
    (`approval_notice`)가 같은 문구를 쓴다."""
    return Message(
        messages.event("거부", approval_line(a, env)), kind="approval", account_id=a["account_id"]
    )


def hedge(
    *, entering: bool, account: str, market: str, label: str, qty: Any, detail: str = ""
) -> str:
    """'[헤지 진입] paper-main · 인버스 ETF(114800)' + 수량·국면 줄. 해제는 '[헤지 해제]'."""
    return messages.render(
        "헤지 진입" if entering else "헤지 해제",
        f"{account} · {label}",
        [f"{'매수' if entering else '매도'} {qty}주", detail],
    )


# --- 승인 알림 버튼(텔레그램 콜백 `apv:*`, `telegram.handle_callback` 이 받는다) ----------------

APPROVAL_PREFIX = "apv"


def approval_buttons(approval_id: int, env: str | None) -> list[list[tuple[str, str]]]:
    """모의는 바로 승인·거절, 실전(모르는 env 포함)은 2단계 — 먼저 확인 버튼을 받는다."""
    p = f"{APPROVAL_PREFIX}:{approval_id}"
    if env == "paper":
        return [[("승인", f"{p}:yes"), ("거절", f"{p}:no")]]
    return [[("실전 승인…", f"{p}:ask"), ("거절", f"{p}:no")]]


def real_confirm_button(approval_id: int, account_id: str) -> list[list[tuple[str, str]]]:
    """실전 2단계 확인 버튼: 데이터에 계좌 id 가 실려 `decide(confirm=…)` 로 간다."""
    data = f"{APPROVAL_PREFIX}:{approval_id}:yes:{account_id}"
    return [[(f"실전 {account_id} 승인 확인", data)]]


def decision_link(public_base: str | None, decision_id: int) -> str | None:
    """폰에서 여는 결정 화면 주소. 외부 주소 설정이 없으면 None."""
    if not public_base:
        return None
    return f"{public_base.rstrip('/')}/decisions/spot/{decision_id}"
