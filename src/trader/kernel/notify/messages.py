"""텔레그램 알림 문구 베이스. 모든 알림 문장은 여기서 만든다 — 호출부는 값만 넘긴다.

형식: `[태그] 제목 한 줄` 다음에 항목 줄들, 행동 안내는 빈 줄 뒤. HTML 이스케이프는
`TelegramNotifier.send` 가 한다(여기서는 평문). 금액은 KR '71,500원', US '$227.55'.

어투: 안내·결과 문장은 해요체, 제목·항목 줄은 명사구. 내부 코드값(`auto`·`bear`·`RISK_OFF` 등)은
아래 표로 한글로 바꿔 보이고 그대로 내보내지 않는다. 기능별 문구(주문·승인·결정)는 각 기능이 더한다.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from trader.kernel.config.time import KST

SIDE = {"BUY": "매수", "SELL": "매도"}
# HOLD 는 '관망' — 보유 수량의 '보유' 와 겹치지 않게
ACTION = {"BUY": "매수", "SELL": "매도", "HOLD": "관망", "ADD": "추가 매수", "TRIM": "일부 매도"}
MODE = {"auto": "자동", "approve": "승인", "off": "끔"}
REGIME = {"bull": "상승", "neutral": "중립", "bear": "하락"}
STANCE = {"RISK_ON": "위험 선호", "RISK_OFF": "위험 회피", "NEUTRAL": "중립"}
DRY_RUN = "주문 없이 판정"
ENV = {"paper": "모의", "real": "실전"}
REAL_CONFIRM = "실전"  # 텔레그램에서 실전 승인에 붙여야 하는 확인어 (/approve 12 실전)


def money(v: Any, market: str | None) -> str:
    """금액·가격 표기. 없으면 '—'. KR 정수 원, US $ 소수 2자리."""
    if v is None or v == "":
        return "—"
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return str(v)
    if market == "US":
        return f"${d:,.2f}"
    return f"{int(d.to_integral_value()):,}원"


def pct(v: Any, digits: int = 0) -> str:
    try:
        return f"{float(v) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


def first_sentences(text: str | None, n: int = 2, limit: int = 160) -> str:
    """근거 앞 n 문장(없으면 앞 limit 자)."""
    t = " ".join((text or "").split())
    if not t:
        return ""
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", t) if p.strip()]
    out = " ".join(parts[:n]) if parts else t
    return out if len(out) <= limit else out[: limit - 1] + "…"


def kst_hhmm(iso: str | None) -> str:
    """UTC ISO → 'HH:MM'(KST). 해석 못 하면 원문."""
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).astimezone(KST).strftime("%H:%M")
    except (TypeError, ValueError):
        return str(iso)


def render(
    tag: str,
    title: str = "",
    lines: list[str] | tuple[str, ...] = (),
    *,
    action: str | None = None,
) -> str:
    """공통 틀. 빈 줄은 버리고, 행동 안내 앞에는 빈 줄 하나."""
    parts = [f"[{tag}] {title}".rstrip()]
    parts += [ln for ln in lines if ln]
    if action:
        parts += ["", action]
    return "\n".join(parts)


def event(tag: str, text: str = "", *lines: str) -> str:
    """종목·주문이 없는 운영 알림(스캔·국면·학습·잡 …)."""
    return render(tag, text, lines)


def failure(tag: str, scope: str, err: Any) -> str:
    """실패 알림: '[태그] 범위' + 오류 한 줄. 예외면 '형식: 메시지'."""
    msg = f"{type(err).__name__}: {err}" if isinstance(err, BaseException) else str(err)
    return render(tag, scope, [f"오류: {msg}"] if msg else [])


def mode_changed(*, account: str, mode: str) -> str:
    return render("모드", f"{account} → {MODE.get(mode, mode)}")
