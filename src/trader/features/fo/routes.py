"""fo 웹 조회(토큰 없음·읽기 전용). 웹은 브로커를 부르지 않고 원장(`fo_positions`·`fo_order_legs`·
`fo_decisions`·`fo_nav_snapshot`·`fo_positions_snapshot`)과 체크포인트만 읽는다.

`fo_router`: `GET /fo/legs?account_id=&date=`·`GET /fo/summary`·`GET /fo/today?date=`(옛
`web/routers/fo.py` 조회 이식 — `account`·`scorecard`·`report` 는 Task 11 `analytics/fo/*`).
`RESOURCES`: `/accounts/{id}/positions` 파생 조각(`fo_rows`, 파생 계좌만). 계좌는
`deps.ports["accounts"].fo_accounts()`. 파생 NAV 는 여기(`/fo/summary`)서 보인다 — accounts 통합
현황은 `account_nav` 만 읽는다(T6-R2). SSE `fo`: 옛 지문 그대로."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from trader.features.fo import store, triggers
from trader.features.fo.executor import VEHICLES, fo_mode, halted_key, verified_key
from trader.features.fo.types import FoPositionRow
from trader.kernel.broker.fo import CONTRACT_VERSION
from trader.kernel.config import KST
from trader.kernel.execution import ledger
from trader.kernel.ledger import control
from trader.kernel.registry.types import ResourcePart, SseKey
from trader.kernel.session import run_store

fo_router = APIRouter(prefix="/fo", tags=["fo"])

MARKET = "KR"
# 게이트 판정(FoVerdict) 중 화면에 펼쳐 보이는 값
VERDICT_FIELDS = (
    "leverage_used", "exposure_after", "stress_loss_after", "lifetime_budget",
    "cross_factor", "n_target", "approved_mult", "code", "kind", "contract_side",
)  # fmt: skip


def _fo_accounts(deps: Any) -> list:
    port = (deps.ports or {}).get("accounts")
    fo = getattr(port, "fo_accounts", None) if port is not None else None
    return list(fo()) if fo else []


def _fo_account(deps: Any, account_id: str) -> Any:
    acct = next((a for a in _fo_accounts(deps) if a.id == account_id), None)
    if acct is None:
        raise HTTPException(status_code=400, detail=f"알 수 없는 파생 계좌: {account_id}")
    return acct


def _today(deps: Any) -> date:
    return deps.now().astimezone(KST).date()


def _s(v: Any) -> Any:
    return str(v) if isinstance(v, Decimal) else v


def _decision_row(r: sqlite3.Row) -> dict:
    try:
        data = json.loads(r["data"]) if r["data"] else {}
    except ValueError:
        data = {}
    dec = data.get("decision") or {}
    return {
        "id": r["id"], "date": r["date"], "account_id": r["account_id"],
        "position_id": r["position_id"], "round": r["round"], "intent": r["intent"],
        "direction": r["direction"], "vehicle": r["vehicle"],
        "target_leverage": r["target_leverage"], "confidence": r["confidence"],
        "index_at_decision": r["index_at_decision"], "stop_pt": dec.get("stop_pt"),
        "take_profit_pt": dec.get("take_profit_pt"), "horizon_days": dec.get("horizon_days"),
        "rationale": dec.get("rationale"), "vs_spot": dec.get("vs_spot"),
        "record_only": data.get("record_only"), "created_at": r["created_at"],
    }  # fmt: skip


# --- positions 조각 ---------------------------------------------------------------------


def _pct(level: Decimal, avg: Decimal | None) -> str:
    return f" −{abs(level / avg - 1) * 100:.1f}%" if avg else ""


def stop_of(p: FoPositionRow, avg: Decimal | None, cfg: Any, today: date) -> dict | None:
    """엔진 보호선과 같은 순서의 손절 표시. 선물은 가격 손절(`stop_fut`)·지수 손절(`stop_pt`),
    옵션은 프리미엄 보호선(`premium_stop`)·지수 손절 — 평단 단위 값이면 '손절 −N%'. 손절이 없고
    최종거래일을 알면 만기 강제 청산일까지 남은 날 '만기 D-n'(level 없음)."""
    unit = p.stop_fut if p.kind == "FUTURES" else p.premium_stop
    if unit is not None:
        return {"level": str(unit), "source": "손절" + _pct(unit, avg)}
    if p.stop_pt is not None:
        # 선물 가격은 지수와 같은 포인트 단위라 비율을 보인다. 옵션 평단(프리미엄)과는 비교 안 함
        tail = _pct(p.stop_pt, avg) if p.kind == "FUTURES" else " 지수"
        return {"level": str(p.stop_pt), "source": "손절" + tail}
    if p.last_trade_date:
        force = triggers.force_close_day_of(
            p, cfg, last_trade_date=date.fromisoformat(p.last_trade_date)
        )
        return {"level": None, "source": f"만기 D-{(force - today).days}"}
    return None


def fo_rows(deps: Any, q: dict[str, str]) -> list[dict]:
    """`ResourcePart` 조각: 파생 계좌만(그 밖은 빈 목록). `qty`=계약, `avg_price`=포인트(최근
    `fo_positions_snapshot`), `krw_value`=평단 × 계약 × 승수."""
    acct = next((a for a in _fo_accounts(deps) if a.id == q.get("account_id")), None)
    if acct is None:
        return []
    cfg = deps.settings.fo
    mult = Decimal(str(cfg.product_cfg().multiplier))
    today = _today(deps)
    out = []
    with closing(deps.conn_factory()) as conn:
        for p in store.open_positions(conn, acct.id):
            snap = conn.execute(
                "SELECT avg FROM fo_positions_snapshot WHERE account_id = ? AND code = ? "
                "ORDER BY date DESC LIMIT 1",
                (acct.id, p.code),
            ).fetchone()
            avg = Decimal(str(snap["avg"])) if snap and snap["avg"] not in (None, "") else None
            out.append({
                "account_id": acct.id, "asset": "fo", "lane": "fo", "market": MARKET,
                "ticker": p.code, "name": None, "qty": p.qty_held, "avg_price": _s(avg),
                "last_price": None, "currency": "KRW",
                "krw_value": _s(avg * p.qty_held * mult) if avg is not None else None,
                "stop": stop_of(p, avg, cfg, today),
                "target": {"level": str(p.tp_pt), "source": "결정"} if p.tp_pt else None,
                "kind": p.kind, "contract_side": p.contract_side, "direction": p.direction,
                "vehicle": p.vehicle, "status": p.status, "position_id": p.id,
                "last_trade_date": p.last_trade_date, "horizon_until": p.horizon_until,
                "at": p.opened_at or p.created_at,
            })  # fmt: skip
    return out


RESOURCES = (ResourcePart("/accounts/{account_id}/positions", fo_rows),)


# --- 조회 라우터 -------------------------------------------------------------------------


def _account_status(conn: sqlite3.Connection, deps: Any, a: Any, today: date) -> dict:
    """계좌 상태 한 묶음(원장·control 만 — 브로커 호출 없음). state 우선순위: 실전 파생 off →
    레인 꺼짐(disabled) → 검증 전(unverified) → 일일 손실 한도(halted) → 기록만(record_only:
    모드 off·일시정지) → active."""
    cfg = deps.settings.fo
    mode, note = fo_mode(conn, a, deps.ports)
    paused = bool(deps.ports["accounts"].paused(conn))
    verified = {
        v: control.get_flag(conn, verified_key(deps.settings, a, v)) == CONTRACT_VERSION
        for v in VEHICLES
    }
    halted = control.get_flag(conn, halted_key(a.id)) == today.isoformat()
    pending = isinstance(control.get_flag(conn, f"fo_pending_entry:{a.id}"), dict)
    nav_row = conn.execute(
        "SELECT date, nav, cash, margin_total, add_margin FROM fo_nav_snapshot "
        "WHERE account_id = ? ORDER BY date DESC LIMIT 1",
        (a.id,),
    ).fetchone()
    nav = Decimal(str(nav_row["nav"])) if nav_row and nav_row["nav"] not in (None, "") else None
    margin = (
        Decimal(str(nav_row["margin_total"]))
        if nav_row and nav_row["margin_total"] not in (None, "")
        else None
    )
    positions = [
        {"code": p.code, "kind": p.kind, "contract_side": p.contract_side,
         "direction": p.direction, "qty": p.qty_held, "status": p.status}
        for p in store.open_positions(conn, a.id)
    ]  # fmt: skip
    if a.env != "paper":
        state = "off"
    elif not cfg.enabled:
        state = "disabled"
    elif not any(verified[v] for v in cfg.vehicles):
        state = "unverified"
    elif halted:
        state = "halted"
    elif mode != "auto" or paused:
        state = "record_only"
    else:
        state = "active"
    rows = conn.execute(
        "SELECT * FROM fo_decisions WHERE date = ? AND (account_id IS NULL OR account_id = ?) "
        "ORDER BY round DESC, id DESC LIMIT 1",
        (today.isoformat(), a.id),
    ).fetchall()
    return {
        "id": a.id, "env": a.env, "mode": mode, "mode_note": note, "paused": paused,
        "state": state, "enabled": cfg.enabled, "vehicles": list(cfg.vehicles),
        "verified": verified, "halted": halted, "pending_entry": pending,
        "nav_date": nav_row["date"] if nav_row else None, "nav": _s(nav),
        "cash": nav_row["cash"] if nav_row else None, "margin_total": _s(margin),
        "add_margin": nav_row["add_margin"] if nav_row else None,
        "margin_pct": round(float(margin / nav), 4) if nav and margin is not None and nav > 0
        else None,
        "positions": positions,
        "decision": _decision_row(rows[0]) if rows else None,
    }  # fmt: skip


@fo_router.get("/summary")
def summary(request: Request) -> dict:
    """파생 계좌마다 상태·잔고(`fo_nav_snapshot`)·포지션·오늘 결정. 계좌 없으면 빈 목록."""
    deps = request.app.state.deps
    today = _today(deps)
    with closing(deps.conn_factory()) as conn:
        return {"accounts": [_account_status(conn, deps, a, today) for a in _fo_accounts(deps)]}


@fo_router.get("/today")
def today_view(request: Request, date_: str | None = Query(None, alias="date")) -> dict:
    """오늘(또는 date) 아침 결정·재평가 라운드·계좌별 보류/일일 손실·게이트·주문 체크포인트."""
    deps = request.app.state.deps
    d = date.fromisoformat(date_) if date_ else _today(deps)
    with closing(deps.conn_factory()) as conn:
        rows = conn.execute(
            "SELECT * FROM fo_decisions WHERE date = ? ORDER BY round, id", (d.isoformat(),)
        ).fetchall()
        decisions = [_decision_row(r) for r in rows]
        accounts = [
            {"id": a.id, "env": a.env,
             "halted": control.get_flag(conn, halted_key(a.id)) == d.isoformat(),
             "pending_entry": isinstance(
                 control.get_flag(conn, f"fo_pending_entry:{a.id}"), dict)}
            for a in _fo_accounts(deps)
        ]  # fmt: skip
    mdir = run_store.market_dir(d, MARKET, deps.state)
    checks = {}
    for stage in run_store.list_checkpoints(mdir):
        if stage.startswith("_fo_risk_check_") or stage.startswith("_fo_order_"):
            data = (run_store.read_checkpoint(mdir, stage) or {}).get("data") or {}
            verdict = data.get("verdict") or {}
            checks[stage] = {
                "approved": verdict.get("approved", data.get("approved")),
                "reasons": (verdict.get("reasons") or data.get("reasons") or [])[:5],
                "note": verdict.get("note") or data.get("note"),
                "status": data.get("status"), "record_only": data.get("record_only"),
                "error": data.get("error"),
                **{k: verdict.get(k) for k in VERDICT_FIELDS if k in verdict},
            }  # fmt: skip
    return {
        "date": d.isoformat(),
        "decision": next((x for x in decisions if x["round"] == 0), None),
        "reevals": [x for x in decisions if x["round"] > 0],
        "accounts": accounts,
        "checks": checks,
    }


@fo_router.get("/legs")
def legs(
    request: Request, account_id: str = Query(...), date_: str | None = Query(None, alias="date")
) -> list[dict]:
    """그날(KST) 만든 주문 다리 + 아직 살아 있는 다리."""
    deps = request.app.state.deps
    acct = _fo_account(deps, account_id)
    d = date.fromisoformat(date_) if date_ else _today(deps)
    lo, hi = ledger.day_bounds(d, MARKET)
    with closing(deps.conn_factory()) as conn:
        rows = conn.execute(
            "SELECT l.*, p.code FROM fo_order_legs l JOIN fo_positions p ON p.id = l.position_id "
            "WHERE l.account_id = ? AND ((l.created_at >= ? AND l.created_at < ?) OR l.status IN "
            "('prepared','sending','accepted','unknown')) ORDER BY l.id DESC",
            (acct.id, lo, hi),
        ).fetchall()
    return [
        {
            "id": x["id"], "position_id": x["position_id"], "code": x["code"],
            "intent": x["intent"], "round": x["round"], "attempt": x["attempt"],
            "status": x["status"], "order_no": x["order_no"], "qty": x["qty"],
            "filled_qty": x["filled_qty"], "price": x["price"], "order_type": x["order_type"],
            "sent_at": x["sent_at"], "created_at": x["created_at"],
        }  # fmt: skip
        for x in rows
    ]


ROUTERS = (fo_router,)


# --- SSE 지문 ---------------------------------------------------------------------------


def _q(conn: sqlite3.Connection, sql: str) -> str:
    row = conn.execute(sql).fetchone()
    return ":".join("" if v is None else str(v) for v in tuple(row))


def _fo_fp(deps: Any) -> str:
    """포지션 버전 합(모든 CAS 갱신)·다리 건수/최대 id/상태별 건수·체결 수량·결정·NAV 값."""
    with closing(deps.conn_factory()) as conn:
        groups = conn.execute(
            "SELECT status, COUNT(*) FROM fo_order_legs GROUP BY status ORDER BY status"
        ).fetchall()
        return "|".join((
            _q(conn, "SELECT COUNT(*), SUM(version), MAX(id) FROM fo_positions"),
            _q(conn, "SELECT COUNT(*), COALESCE(MAX(id), 0) FROM fo_order_legs")
            + ":" + ",".join(f"{g[0]}={g[1]}" for g in groups),
            _q(conn, "SELECT COUNT(*), SUM(filled_qty) FROM fo_order_legs"),
            _q(conn, "SELECT COUNT(*), MAX(id) FROM fo_decisions"),
            _q(conn, "SELECT COUNT(*), MAX(date), SUM(CAST(nav AS REAL)) FROM fo_nav_snapshot"),
        ))  # fmt: skip


SSE_KEYS = (SseKey("fo", _fo_fp),)
