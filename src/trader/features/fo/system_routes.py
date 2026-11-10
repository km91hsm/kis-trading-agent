"""fo 「시스템」 웹 조회(토큰 없음·읽기 전용): `GET /system/fo`(옛 `/fo/account`, T11-R1).

파생 계좌(`ports["accounts"].fo_accounts()`)마다 상태(`routes._account_status` 의
id·env·mode·state)·오늘 fo 트리거·control 플래그 7종 요약(`feature.CONTROL_NAMESPACES`).
시장·한도 맥락은 하나(오늘 `_fo_inputs` 가 있으면 그것, 없으면 설정·control).
계좌번호는 내보내지 않는다(id 만)."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from typing import Any

from fastapi import APIRouter, Request

from trader.features.fo import store, switch
from trader.features.fo.inputs import INPUTS_STAGE, limits_of
from trader.features.fo.routes import MARKET, _account_status, _fo_accounts, _today
from trader.kernel.execution import ledger
from trader.kernel.ledger import control
from trader.kernel.session import run_store

system_fo_router = APIRouter(prefix="/system", tags=["system"])

STATUS_KEYS = ("id", "env", "mode", "mode_note", "paused", "state", "enabled")


def _flags(conn: sqlite3.Connection, a: Any, status: dict, today_iso: str) -> dict:
    """control 플래그 7종 요약(값 자체는 내부 상태라 화면에 필요한 모양으로만)."""
    day_open = control.get_flag(conn, f"fo_day_open:{a.id}")
    alerts = control.get_flag(conn, f"fo_alerts:{a.id}") or {}
    ref_iv = {}
    for p in store.open_positions(conn, a.id):
        iv = control.get_flag(conn, f"fo_ref_iv:{p.id}")
        if iv is not None:
            ref_iv[str(p.id)] = iv
    prefix = f"fo_replace:{a.id}:"
    replace = []
    for r in conn.execute(
        "SELECT key, value FROM control WHERE substr(key, 1, ?) = ?", (len(prefix), prefix)
    ):
        suffix = r["key"][len(prefix):]
        if suffix.isdigit() and json.loads(r["value"]) is not None:
            replace.append(int(suffix))
    replace.sort()
    return {
        "fo_verified": status["verified"],
        "fo_halted": status["halted"],
        "fo_day_open": day_open.get("date") if isinstance(day_open, dict) else None,
        "fo_alerts": len(alerts.get("keys") or []) if alerts.get("date") == today_iso else 0,
        "fo_ref_iv": ref_iv,
        "fo_pending_entry": status["pending_entry"],
        "fo_replace": replace,
    }


def _triggers(conn: sqlite3.Connection, account_id: str, lo: str, hi: str) -> list[dict]:
    return [
        {"kind": str(r["kind"]).removeprefix("fo:"), "code": r["ticker"],
         "price": r["price"], "at": r["created_at"], "handled": r["handled_at"] is not None,
         "session": r["session_id"]}
        for r in conn.execute(
            "SELECT * FROM triggers WHERE kind LIKE 'fo:%' AND account_id = ? "
            "AND created_at >= ? AND created_at < ? ORDER BY id DESC",
            (account_id, lo, hi),
        )
    ]  # fmt: skip


def _context(deps: Any, conn: sqlite3.Connection, today: Any) -> dict:
    mdir = run_store.market_dir(today, MARKET, deps.state)
    inputs = run_store.read_checkpoint(mdir, INPUTS_STAGE)
    ctx = (((inputs or {}).get("data") or {}).get("context") or {}).get("data") or {}
    if ctx:
        return {
            "source": "inputs", "index": ctx.get("index"), "regime": ctx.get("regime"),
            "market_view": ctx.get("market_view"), "limits": ctx.get("limits"),
            "products": ctx.get("products"),
        }  # fmt: skip
    regime = control.get_flag(conn, f"regime:{MARKET}") or {}
    view = control.get_flag(conn, f"market_view:{MARKET}") or {}
    rstate = regime.get("state") if isinstance(regime, dict) else None
    return {
        "source": "settings", "index": None,
        "regime": {"state": rstate,
                   "count": regime.get("count") if isinstance(regime, dict) else None},
        "market_view": {k: view.get(k) for k in ("stance", "confidence", "date")}
        if isinstance(view, dict) and view else None,
        "limits": {**limits_of(deps.settings.fo, rstate),
                   "enabled": switch.any_enabled(conn, deps.settings, _fo_accounts(deps))},
        "products": None,
    }  # fmt: skip


@system_fo_router.get("/fo")
def system_fo(request: Request) -> dict:
    """파생 레인 상태: 계좌마다 상태·플래그 요약·오늘 트리거 + 시장·한도 맥락."""
    deps = request.app.state.deps
    today = _today(deps)
    lo, hi = ledger.day_bounds(today, MARKET)
    out = []
    with closing(deps.conn_factory()) as conn:
        for a in _fo_accounts(deps):
            st = _account_status(conn, deps, a, today)
            out.append({
                **{k: st[k] for k in STATUS_KEYS},
                "flags": _flags(conn, a, st, today.isoformat()),
                "triggers": _triggers(conn, a.id, lo, hi),
            })  # fmt: skip
        context = _context(deps, conn, today)
    return {"accounts": out, "context": context}
