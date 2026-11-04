"""파생 레인 상태 한 묶음: 텔레그램 `/fo`·`trader fo status`·`/status` 의 「파생(선물옵션)」 절이
같은 줄을 쓴다. 원장만 읽는다(브로커·네트워크 호출 없음). 일시정지는 포트 없이 conn 만 받으므로
커널 `ledger.control` 의 `paused` 플래그를 직접 본다."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from typing import Any

from trader.features.fo.messages import FO_INTENT
from trader.kernel.config import KST
from trader.kernel.ledger import control
from trader.kernel.registry.types import StatusSection

logger = logging.getLogger("trader.fo.status")

_VEHICLE = {"FUTURES": "선물", "OPTION_BUY": "옵션 매수", "OPTION_SELL": "옵션 매도"}
_DIRECTION = {"LONG": "롱", "SHORT": "숏", "FLAT": "관망"}
_KIND = {"FUTURES": "", "CALL": "콜 ", "PUT": "풋 "}
_STATUS = {
    "entering": "진입 중", "held": "보유", "resizing": "수량 조정 중", "closing": "청산 중",
    "exit_failed": "청산 실패", "awaiting_settlement": "정산 대기", "unknown": "대조 필요",
}  # fmt: skip


def lines(conn: sqlite3.Connection, now: datetime) -> list[str]:
    today = now.astimezone(KST).date().isoformat()
    out: list[str] = []
    rows = conn.execute(
        "SELECT * FROM fo_positions WHERE status NOT IN ('settled','closed') "
        "ORDER BY account_id, id"
    ).fetchall()
    for r in rows:
        lev = (
            f" · 승인 배수 {float(r['leverage_used']):.2f}"
            if r["leverage_used"] is not None
            else ""
        )
        stop = f" · 지수 손절 {r['stop_pt']}" if r["stop_pt"] else ""
        ps = f" · 프리미엄 보호선 {r['premium_stop']}" if r["premium_stop"] else ""
        side = "매수" if r["contract_side"] == "LONG" else "매도"
        out.append(
            f"- {r['account_id']} {r['code']} {_DIRECTION.get(r['direction'], r['direction'])} · "
            f"{_KIND.get(r['kind'], '')}{_VEHICLE.get(r['vehicle'], r['vehicle'])} · "
            f"{side} {r['qty_held']}계약 [{_STATUS.get(r['status'], r['status'])}]{lev}{stop}{ps}"
        )
    if not rows:
        out.append("- 보유 없음")
    dec = conn.execute(
        "SELECT intent, direction, vehicle, target_leverage, confidence FROM fo_decisions "
        "WHERE date = ? AND round = 0 ORDER BY id DESC LIMIT 1",
        (today,),
    ).fetchone()
    if dec is not None:
        out.append(
            f"- 오늘 결정: {FO_INTENT.get(dec['intent'], dec['intent'])} "
            f"{_DIRECTION.get(dec['direction'], dec['direction'])} "
            f"{_VEHICLE.get(dec['vehicle'], dec['vehicle'])} 배수 {dec['target_leverage']} "
            f"확신 {dec['confidence']}"
        )
    else:
        out.append("- 오늘 결정: 없음")
    n_re = conn.execute(
        "SELECT COUNT(*) FROM fo_decisions WHERE date = ? AND round > 0", (today,)
    ).fetchone()[0]
    if n_re:
        out.append(f"- 오늘 재평가 {n_re}회")
    flags = conn.execute(
        "SELECT key, value FROM control WHERE key LIKE 'fo_halted:%' OR key LIKE "
        "'fo_pending_entry:%'"
    ).fetchall()
    for f in flags:
        key, val = f["key"], f["value"]
        if key.startswith("fo_halted:") and val.strip('"') == today:
            out.append(f"- {key.split(':', 1)[1]}: 일일 손실 한도 — 오늘 신규 금지")
        elif key.startswith("fo_pending_entry:") and val not in ("null", ""):
            out.append(f"- {key.split(':', 1)[1]}: 진입 창 대기 중인 아침 결정 있음")
    unknown = sum(1 for r in rows if r["status"] in ("unknown", "exit_failed"))
    if unknown:
        out.append(f"- 사람 확인 필요: unknown·청산 실패 {unknown}건(`trader fo adopt|close`)")
    if control.get_flag(conn, "paused", False):
        out.append("- 일시정지 중 — 주문 없이 기록만")
    return out


def render(ctx: Any) -> list[str]:
    """`/status` 절 본문. 조회가 깨져도 /status 는 계속된다(오류 한 줄)."""
    try:
        return lines(ctx.conn, ctx.now())
    except Exception as e:  # noqa: BLE001 - 상태 조회는 실패해도 /status 는 계속
        logger.exception("파생 상태 조회 실패")
        return [f"- 파생 조회 실패: {type(e).__name__}: {e}"]


STATUS_SECTION = StatusSection("파생(선물옵션)", render)
