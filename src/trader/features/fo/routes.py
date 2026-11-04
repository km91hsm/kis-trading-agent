"""fo 웹 조회(토큰 없음·읽기 전용). 웹은 브로커를 부르지 않고 원장(`fo_positions`·`fo_order_legs`·
`fo_decisions`·`fo_nav_snapshot`·`fo_positions_snapshot`)과 체크포인트만 읽는다.

`fo_router`: `GET /fo/legs?account_id=&date=`·`GET /fo/summary`·`GET /fo/today?date=`(옛
`web/routers/fo.py` 조회 이식 — `scorecard`·`report` 는 `analytics_routes.py`, `account` 는
`system_routes.py` 의 `/system/fo`).
`decisions_router`: `GET /decisions/fo/{id}`(결정 + 봉투 다리)·`/decisions/fo/{id}/trace`(fo
체크포인트). `RESOURCES`: `/accounts/{id}/positions` 파생 조각(`fo_rows`, 파생 계좌만),
`/decisions`(`fo_decisions`)·`/orders`(`fo_order_legs` 를 (포지션·의도·라운드)로 묶은 행 +
`legs`) 파생 조각. 계좌는
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


# --- decisions·orders 조각 ----------------------------------------------------------------


def _parse_date(value: str) -> date:
    """조회 `date`(YYYY-MM-DD). 형식이 틀리면 400(500 이 아니라)."""
    try:
        return date.fromisoformat(value)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"date 형식 오류(YYYY-MM-DD): {value!r}") from e


def _day(deps: Any, q: dict[str, str]) -> date | None:
    """조회 거래일(KST). `market` 이 KR 이 아니거나 `account_id` 가 파생 계좌가 아니면(현물·모르는
    계좌) None — 파생은 KR·파생 계좌만(`fo_rows` 와 같은 규칙)."""
    if (q.get("market") or MARKET).upper() != MARKET:
        return None
    if q.get("account_id") and q["account_id"] not in {a.id for a in _fo_accounts(deps)}:
        return None
    return _parse_date(q["date"]) if q.get("date") else _today(deps)


def _decision_status(conn: sqlite3.Connection, r: sqlite3.Row) -> str | None:
    """그 결정 봉투(request_id)로 만든 마지막 다리 상태 → 없으면 기록만이면 `record_only`."""
    leg = conn.execute(
        "SELECT status FROM fo_order_legs WHERE request_id = ? ORDER BY id DESC LIMIT 1",
        (r["request_id"],),
    ).fetchone()
    if leg is not None:
        return leg["status"]
    try:
        data = json.loads(r["data"]) if r["data"] else {}
    except ValueError:
        data = {}
    return "record_only" if data.get("record_only") else None


def fo_decision_rows(deps: Any, q: dict[str, str]) -> list[dict]:
    """`ResourcePart("/decisions")` 파생 조각: 그날 `fo_decisions`. `account_id` 를 주면 그 계좌
    결정 + 계좌 없는 아침 결정."""
    d = _day(deps, q)
    if d is None:
        return []
    sql, args = "SELECT * FROM fo_decisions WHERE date = ?", [d.isoformat()]
    if q.get("account_id"):
        sql += " AND (account_id IS NULL OR account_id = ?)"
        args.append(q["account_id"])
    with closing(deps.conn_factory()) as conn:
        return [
            {
                "asset": "fo", "id": r["id"], "date": r["date"], "account_id": r["account_id"],
                "intent": r["intent"], "status": _decision_status(conn, r),
                "at": r["created_at"], "market": MARKET, "lane": "fo", "round": r["round"],
                "direction": r["direction"], "vehicle": r["vehicle"],
                "confidence": r["confidence"],
            }  # fmt: skip
            for r in conn.execute(sql + " ORDER BY id", args).fetchall()
        ]


def _leg(x: sqlite3.Row) -> dict:
    return {
        "id": x["id"], "attempt": x["attempt"], "status": x["status"], "order_id": x["order_id"],
        "order_no": x["order_no"], "qty": x["qty"], "filled_qty": x["filled_qty"],
        "price": x["price"], "order_type": x["order_type"], "sent_at": x["sent_at"],
        "created_at": x["created_at"],
    }  # fmt: skip


def fo_order_rows(deps: Any, q: dict[str, str]) -> list[dict]:
    """`ResourcePart("/orders")` 파생 조각: 그날(KST) 만든 다리를 (포지션·의도·라운드)로 묶어
    현물 주문과 같은 모양 + `legs`(시도들). 상태·수량·가격은 마지막 시도, 체결은 합.
    쿼리 `date`·`account_id`·`status`(쉼표로 여럿, 행 상태 기준)."""
    d = _day(deps, q)
    if d is None:
        return []
    lo, hi = ledger.day_bounds(d, MARKET)
    sql = (
        "SELECT l.*, p.code, o.side FROM fo_order_legs l JOIN fo_positions p "
        "ON p.id = l.position_id LEFT JOIN orders o ON o.id = l.order_id "
        "WHERE l.created_at >= ? AND l.created_at < ?"
    )
    args: list[Any] = [lo, hi]
    if q.get("account_id"):
        sql += " AND l.account_id = ?"
        args.append(q["account_id"])
    statuses = {s for s in (q.get("status") or "").split(",") if s}
    groups: dict[tuple, list[sqlite3.Row]] = {}
    with closing(deps.conn_factory()) as conn:
        for x in conn.execute(sql + " ORDER BY l.id", args).fetchall():
            groups.setdefault((x["position_id"], x["intent"], x["round"]), []).append(x)
    out = []
    for legs in groups.values():
        first, last = legs[0], legs[-1]
        if statuses and last["status"] not in statuses:
            continue
        side = next((x["side"] for x in reversed(legs) if x["side"]), None)
        out.append({
            "asset": "fo", "id": first["id"], "account_id": first["account_id"],
            "market": MARKET, "ticker": first["code"], "side": side, "qty": last["qty"],
            "price": last["price"], "order_type": last["order_type"], "status": last["status"],
            "order_no": last["order_no"], "origin": "fo", "lane": "fo",
            "position_id": first["position_id"], "intent": first["intent"],
            "round": first["round"], "filled_qty": sum(int(x["filled_qty"] or 0) for x in legs),
            "legs": [_leg(x) for x in legs], "at": first["created_at"],
        })  # fmt: skip
    return out


RESOURCES = (
    ResourcePart("/accounts/{account_id}/positions", fo_rows),
    ResourcePart("/decisions", fo_decision_rows),
    ResourcePart("/orders", fo_order_rows),
)


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


def _checks(mdir: Any, stages: list[str], account_id: str | None = None) -> dict:
    """게이트·주문 체크포인트(`_fo_risk_check_<계좌>`·`_fo_order_<계좌>`) 요약. account_id 를 주면
    그 계좌 것만."""
    checks = {}
    for stage in stages:
        for prefix in ("_fo_risk_check_", "_fo_order_"):
            if stage.startswith(prefix) and account_id in (None, stage[len(prefix):]):
                break
        else:
            continue
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
    return checks


@fo_router.get("/today")
def today_view(request: Request, date_: str | None = Query(None, alias="date")) -> dict:
    """오늘(또는 date) 아침 결정·재평가 라운드·계좌별 보류/일일 손실·게이트·주문 체크포인트."""
    deps = request.app.state.deps
    d = _parse_date(date_) if date_ else _today(deps)
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
    checks = _checks(mdir, run_store.list_checkpoints(mdir))
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
    d = _parse_date(date_) if date_ else _today(deps)
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


decisions_router = APIRouter(prefix="/decisions/fo", tags=["decisions"])


def _fo_decision(conn: sqlite3.Connection, decision_id: int) -> sqlite3.Row:
    r = conn.execute("SELECT * FROM fo_decisions WHERE id = ?", (decision_id,)).fetchone()
    if r is None:
        raise HTTPException(status_code=404, detail=f"파생 결정 없음: {decision_id}")
    return r


@decisions_router.get("/{decision_id}")
def decision_detail(request: Request, decision_id: int) -> dict:
    """파생 결정 1건(화면 펼침 값) + 원본 `data` + 그 결정 봉투(request_id)로 만든 다리."""
    with closing(request.app.state.deps.conn_factory()) as conn:
        r = _fo_decision(conn, decision_id)
        legs = conn.execute(
            "SELECT * FROM fo_order_legs WHERE request_id = ? ORDER BY id", (r["request_id"],)
        ).fetchall()
        status = _decision_status(conn, r)
    try:
        data = json.loads(r["data"]) if r["data"] else {}
    except ValueError:
        data = {}
    return {
        "decision": {**_decision_row(r), "asset": "fo", "status": status,
                     "request_id": r["request_id"]},
        "data": data,
        "legs": [{**_leg(x), "intent": x["intent"], "round": x["round"]} for x in legs],
    }  # fmt: skip


@decisions_router.get("/{decision_id}/trace")
def decision_trace(request: Request, decision_id: int) -> dict:
    """그날 fo 체크포인트로 본 판단 과정: 단계 목록(`_fo*`)·애널리스트·결정 체크포인트(아침
    `_fo_decision`, 재평가 `_fo_decision_r<n>_<계좌>`)·게이트/주문 요약(계좌 결정이면 그 계좌)."""
    deps = request.app.state.deps
    with closing(deps.conn_factory()) as conn:
        r = _fo_decision(conn, decision_id)
    mdir = run_store.market_dir(date.fromisoformat(r["date"]), MARKET, deps.state)
    stages = [s for s in run_store.list_checkpoints(mdir) if s.startswith("_fo")]

    def data(stage: str) -> dict:
        d = (run_store.read_checkpoint(mdir, stage) or {}).get("data")
        return d if isinstance(d, dict) else {}

    analysts = [
        {"role": s[len("_fo_analyst_"):], **{k: data(s).get(k)
                                             for k in ("signal", "confidence", "summary")}}
        for s in stages if s.startswith("_fo_analyst_")
    ]  # fmt: skip
    stage = (
        "_fo_decision" if r["round"] == 0
        else f"_fo_decision_r{r['round']}_{r['account_id']}"
    )  # fmt: skip
    return {
        "decision": {**_decision_row(r), "asset": "fo"},
        "stages": stages,
        "analysts": analysts,
        "checkpoint": data(stage) if stage in stages else None,
        "checks": _checks(mdir, stages, r["account_id"]),
    }


ROUTERS = (fo_router, decisions_router)


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
