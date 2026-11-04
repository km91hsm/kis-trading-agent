"""spot 보유 표: 계좌 보유 행(공통 모양 + 계획·비중·드리프트)·실제 적용 손절선·목표, 전체 합산
(`overview`), 위험 카드(`risk_card`). 원장·포트만 읽는다(브로커·네트워크 호출 없음).

옛 `ledger.views.position_plan·position_stop·attach_plans·account_overview·sector_weights` 와
`ledger.risk_card` 이식. 손절선은 장중 모니터와 같은 `stops.effective_stop`(바닥 = 보유 묶음 시작
뒤 결정들의 손절 최고값)이고, 출처는 화면 문구 "결정"·"추적"·"기본 −N%" 로 바꾼다. 헤지·현금
운용 ETF(`excluded_tickers`)는 모니터가 손절하지 않으므로 stop 이 없다. 종가는 `ports["ml"]
.close_table`, 섹터는 `ports["scan"]`(없으면 전부 '기타')."""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import closing
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from trader.features.spot import ledger as spot_ledger
from trader.features.spot.excluded import excluded_tickers
from trader.features.spot.executor import sector_of_for
from trader.features.spot.stops import effective_stop
from trader.kernel.execution import horizon
from trader.kernel.execution import ledger as xledger
from trader.kernel.ledger import names

MARKETS = ("KR", "US")
CURRENCY = {"KR": "KRW", "US": "USD"}
SOURCE = {"decision": "결정", "floor": "추적"}


def _dec(v: Any) -> Decimal | None:
    if v in (None, ""):
        return None
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def dist(target: Any, current: Any) -> float | None:
    """목표가의 현재가 대비 거리(소수). 어느 쪽이든 없거나 0 이하이면 None."""
    t, c = _dec(target), _dec(current)
    if t is None or c is None or t <= 0 or c <= 0:
        return None
    return float(t / c - 1)


def pct_label(pct: float) -> str:
    """기본 손절 비율 표기: -0.15 → '−15%'(소수 자리는 필요할 때만)."""
    return f"−{(abs(Decimal(str(pct))) * 100).normalize():f}%"


def plan_of(decision: dict | None, current_price: Any) -> dict | None:
    """보유 행의 계획: 최근 결정의 목표 비중·익절·손절·지정가·보유기간과 현재가 대비 거리.
    결정이 없거나 헤지 결정이면 None."""
    if not decision or decision.get("source") == "hedge":
        return None
    data = decision.get("data") or {}
    days = data.get("horizon_days")
    return {
        "decision_id": decision.get("id"),
        "date": decision.get("date"),
        "action": decision.get("action"),
        "target_weight": data.get("target_weight", decision.get("target_weight")),
        "take_profit": data.get("take_profit"),
        "stop_loss": data.get("stop_loss"),
        "limit_price": data.get("limit_price"),
        "horizon_days": days,
        "horizon": horizon.name_of(days),
        "dist_take_profit_pct": dist(data.get("take_profit"), current_price),
        "dist_stop_pct": dist(data.get("stop_loss"), current_price),
        "dist_limit_pct": dist(data.get("limit_price"), current_price),
    }


def stop_of(
    conn: sqlite3.Connection,
    market: str,
    account_id: str,
    ticker: str,
    *,
    avg_price: Any,
    decision: dict | None,
    stop_loss_pct: float,
) -> dict | None:
    """실제 적용 손절선 `{level, source}` — 모니터와 같은 계산. 근거가 없으면 None."""
    since = xledger.held_since(conn, account_id, [ticker]).get(ticker)
    floor = (
        spot_ledger.max_stop_since(conn, market, ticker, None, since_date=since)
        if since is not None
        else None
    )
    data = (decision or {}).get("data") or {}
    level, source = effective_stop(data.get("stop_loss"), floor, _dec(avg_price), stop_loss_pct)
    if level is None:
        return None
    label = SOURCE.get(source or "") or f"기본 {pct_label(stop_loss_pct)}"
    return {"level": str(level), "source": label}


def target_of(decision: dict | None) -> dict | None:
    """목표 = 최근 결정의 익절가(출처 '결정'). 없으면 None."""
    if not decision or decision.get("source") == "hedge":
        return None
    tp = ((decision.get("data") or {}).get("take_profit"))
    return {"level": str(_dec(tp)), "source": "결정"} if _dec(tp) else None


def _stock_account(deps: Any, account_id: str | None) -> Any | None:
    port = (deps.ports or {}).get("accounts")
    if port is None or not account_id:
        return None
    return next((a for a in port.accounts() if a.id == account_id), None)


def account_rows(
    conn: sqlite3.Connection, deps: Any, account: Any, market: str
) -> list[dict]:
    """한 계좌·시장의 최신 스냅샷 보유 행(공통 모양 + plan·weight·drift_pp·horizon·평가)."""
    snap = xledger.latest_positions(conn, account.id, market)
    if not snap:
        return []
    stop_pct = deps.settings.risk.stop_loss_pct
    excluded = excluded_tickers(deps.settings, (deps.ports or {}).get("regime"), market)
    nav = _dec(snap.get("nav")) or Decimal(0)
    out = []
    for p in snap["positions"]:
        ticker = p["ticker"]
        latest = xledger.latest_decision(conn, market, ticker)
        plan = plan_of(latest, p.get("current_price"))
        ev = _dec(p.get("eval_amount")) or Decimal(0)
        weight = float(ev / nav) if nav > 0 else None
        tgt = (plan or {}).get("target_weight")
        drift = (
            round((weight - float(tgt)) * 100, 2)
            if weight is not None and tgt is not None and plan["action"] in ("BUY", "ADD", "TRIM")
            else None
        )
        out.append({
            "account_id": account.id, "asset": "stock", "lane": "spot", "market": market,
            "ticker": ticker, "name": names.resolve(conn, market, ticker),
            "qty": int(p["qty"] or 0), "avg_price": p["avg_price"], "last_price": None,
            "currency": CURRENCY.get(market, "KRW"),
            "stop": None if ticker in excluded else stop_of(
                conn, market, account.id, ticker, avg_price=p.get("avg_price"),
                decision=latest, stop_loss_pct=stop_pct,
            ),
            "target": target_of(latest),
            "horizon": plan["horizon"] if plan else None,
            "horizon_days": plan["horizon_days"] if plan else None,
            "plan": plan, "weight": weight, "drift_pp": drift,
            "current_price": p.get("current_price"), "eval_amount": p.get("eval_amount"),
            "snapshot_date": snap["date"], "at": snap["date"],
        })  # fmt: skip
    return out


def spot_rows(deps: Any, q: dict[str, str]) -> list[dict]:
    """`ResourcePart` 조각: 현물 계좌만(파생·모르는 계좌는 빈 목록). `market` 이 없으면 계좌의
    모든 시장."""
    account = _stock_account(deps, q.get("account_id"))
    if account is None:
        return []
    market = (q.get("market") or "").upper()
    markets = [market] if market else list(account.markets)
    with closing(deps.conn_factory()) as conn:
        return [
            r for m in markets if m in account.markets for r in account_rows(conn, deps, account, m)
        ]


# --- 전체 합산 -------------------------------------------------------------------------


def sector_weights(
    positions: list[dict],
    nav: Any,
    sector_of: Callable[[str], str | None] | None,
    *,
    hedge_tickers: frozenset[str] | set[str] = frozenset(),
    cash: Any = None,
) -> list[dict]:
    """섹터별 평가금액·비중(비중 내림차순). 모르는 종목 '기타', 헤지 ETF '헤지', 현금 '현금' 행으로
    합이 NAV 가 되게 한다. nav 가 없거나 0 이면 비중은 None. sector_of 가 `auto_tickers` 를 가지면
    그 종목이 든 행에 `auto: true`."""
    nav_d = _dec(nav)
    buckets: dict[str, dict] = {}
    for p in positions:
        t = str(p.get("ticker"))
        sector = "헤지" if t in hedge_tickers else ((sector_of(t) if sector_of else None) or "기타")
        b = buckets.setdefault(sector, {"sector": sector, "eval_amount": Decimal(0), "tickers": []})
        b["eval_amount"] += _dec(p.get("eval_amount")) or Decimal(0)
        b["tickers"].append(t)
    if _dec(cash) is not None:
        buckets["현금"] = {"sector": "현금", "eval_amount": _dec(cash), "tickers": []}
    auto = getattr(sector_of, "auto_tickers", None) or set()
    return [
        {
            "sector": b["sector"], "eval_amount": str(b["eval_amount"]),
            "weight": float(b["eval_amount"] / nav_d) if nav_d and nav_d > 0 else None,
            "tickers": b["tickers"], "count": len(b["tickers"]),
            "auto": any(t in auto for t in b["tickers"]),
        }  # fmt: skip
        for b in sorted(buckets.values(), key=lambda b: (-b["eval_amount"], b["sector"]))
    ]


def _env_block(
    conn: sqlite3.Connection,
    snaps: list[tuple],
    market: str,
    sector_of: Callable[[str], str | None] | None,
    hedge: frozenset[str],
    *,
    stop_loss_pct: float,
    excluded: frozenset[str],
) -> dict:
    """한 시장·한 환경(모의|실전) 계좌들의 합산: NAV·현금·평가손익·보유(같은 종목 합침, 수량 가중
    평단)·섹터 비중. 비중·섹터의 분모는 이 환경의 NAV 뿐이다."""
    nav = cash = cost = ev_sum = Decimal(0)
    merged: dict[str, dict] = {}
    accts = []
    for a, snap in snaps:
        nav += _dec(snap["nav"]) or Decimal(0)
        cash += _dec(snap["cash"]) or Decimal(0)
        accts.append({"id": a.id, "env": a.env, "nav": snap["nav"], "cash": snap["cash"],
                      "snapshot_date": snap["date"]})  # fmt: skip
        for p in snap["positions"]:
            qty = int(p["qty"] or 0)
            if qty <= 0:
                continue
            avg = _dec(p["avg_price"]) or Decimal(0)
            ev = _dec(p["eval_amount"]) or Decimal(0)
            g = merged.setdefault(p["ticker"], {
                "ticker": p["ticker"], "qty": 0, "cost": Decimal(0), "eval": Decimal(0),
                "current_price": p["current_price"], "accounts": [], "lots": [],
            })  # fmt: skip
            g["lots"].append((a.id, p["avg_price"]))
            g["qty"] += qty
            g["cost"] += avg * qty
            g["eval"] += ev
            g["accounts"].append(a.id)
            cost += avg * qty
            ev_sum += ev
    positions = []
    for g in sorted(merged.values(), key=lambda g: -g["eval"]):
        avg = g["cost"] / g["qty"] if g["qty"] else Decimal(0)
        pnl = g["eval"] - g["cost"]
        latest = xledger.latest_decision(conn, market, g["ticker"])
        plan = plan_of(latest, g["current_price"])
        # 계좌마다 보유 시작일·평단이 달라 손절선이 다를 수 있다 — 가장 낮은(가장 늦게 걸리는) 것
        stops = [] if g["ticker"] in excluded else [
            st for acct_id, lot_avg in g["lots"]
            if (st := stop_of(conn, market, acct_id, g["ticker"], avg_price=lot_avg,
                              decision=latest, stop_loss_pct=stop_loss_pct)) is not None
        ]  # fmt: skip
        positions.append({
            "ticker": g["ticker"], "market": market,
            "name": names.resolve(conn, market, g["ticker"]),
            "qty": g["qty"], "avg_price": str(avg), "current_price": g["current_price"],
            "eval_amount": str(g["eval"]), "pnl": str(pnl),
            "pnl_pct": float(pnl / g["cost"]) if g["cost"] else None,
            "weight": float(g["eval"] / nav) if nav > 0 else None, "accounts": g["accounts"],
            "plan": plan,
            "stop": min(stops, key=lambda st: Decimal(st["level"])) if stops else None,
            "target": target_of(latest),
            "horizon_days": plan["horizon_days"] if plan else None,
            "horizon": plan["horizon"] if plan else None,
        })  # fmt: skip
    unmapped = [
        p["ticker"] for p in positions
        if p["ticker"] not in hedge and not (sector_of and sector_of(p["ticker"]))
    ]  # fmt: skip
    pnl_all = ev_sum - cost
    return {
        "nav": str(nav), "cash": str(cash), "pnl": str(pnl_all),
        "pnl_pct": float(pnl_all / cost) if cost else None, "positions": positions,
        "sectors": sector_weights(positions, nav, sector_of, hedge_tickers=hedge, cash=cash),
        "unmapped": unmapped, "accounts": accts,
    }  # fmt: skip


def overview(conn: sqlite3.Connection, deps: Any, markets: list[str] | None = None) -> dict:
    """현물 계좌 전체 합산. 통화가 달라 시장별로, 가짜 돈과 진짜 돈이라 모의·실전(env)별로만 더한다
    — `markets[시장].envs[env]`. 파생 계좌는 포트 `accounts()` 에 없으므로 들어오지 않는다.
    스냅샷이 없는 계좌는 `accounts_missing_snapshot[시장]`, 계좌 설정 수는 `configured`."""
    port = (deps.ports or {}).get("accounts")
    accounts = [a for a in (port.accounts() if port is not None else []) if a.asset != "fo"]
    regime = (deps.ports or {}).get("regime")
    stop_pct = deps.settings.risk.stop_loss_pct
    wanted = markets or list(MARKETS)
    out: dict = {"markets": {}, "accounts_missing_snapshot": {}, "configured": len(accounts)}
    for m in [m for m in wanted if any(m in a.markets for a in accounts)]:
        by_env: dict[str, list[tuple]] = {}
        missing = []
        for a in accounts:
            if m not in a.markets:
                continue
            snap = xledger.latest_positions(conn, a.id, m)
            if snap is None:
                missing.append(a.id)
                continue
            by_env.setdefault(a.env, []).append((a, snap))
        hedge = frozenset(regime.hedge_tickers(m)) if regime is not None else frozenset()
        sector_of = sector_of_for(deps.ports, m)
        excluded = excluded_tickers(deps.settings, regime, m)
        out["markets"][m] = {"envs": {
            env: _env_block(conn, by_env[env], m, sector_of, hedge, stop_loss_pct=stop_pct,
                            excluded=excluded)
            for env in ("paper", "real") if env in by_env
        }}  # fmt: skip
        out["accounts_missing_snapshot"][m] = missing
    out["pending_approvals"] = conn.execute(
        "SELECT COUNT(*) FROM approvals WHERE status='pending'"
    ).fetchone()[0]
    return out


# --- 위험 카드 -------------------------------------------------------------------------

WINDOW = 60
MIN_OBS = 20
CACHE_TTL_S = 300.0
# (계좌, 시장, 기준일, 창) → (버전, 저장 시각, 카드). 계좌 화면은 SSE 마다 다시 읽는데 종가표
# 계산이 무겁다 — 스냅샷이 같고 TTL 안이면 같은 카드
_CACHE: dict[tuple, tuple[tuple, float, dict]] = {}
_LOCK = threading.Lock()


def _version(conn: sqlite3.Connection, account_id: str, market: str) -> tuple:
    # MAX(created_at): 같은 날 upsert(평가액 변경)는 행 수·id 를 바꾸지 않지만 created_at 은 바뀐다
    return tuple(conn.execute(
        "SELECT COUNT(*), MAX(id), MAX(created_at) FROM positions_snapshot "
        "WHERE account_id=? AND market=? AND asset='stock'",
        (account_id, market),
    ).fetchone())  # fmt: skip


def risk_card(
    conn: sqlite3.Connection, ml: Any, *, account_id: str, market: str, today: date,
    window: int = WINDOW,
) -> dict:  # fmt: skip
    """`_risk` 결과를 (계좌·시장·기준일·창)별로 기억한다(스냅샷 버전이 바뀌거나 TTL 이
    지나면 다시 계산)."""
    key = (account_id, market, today, window, id(ml))
    version = _version(conn, account_id, market)
    now = time.monotonic()
    with _LOCK:
        hit = _CACHE.get(key)
        if hit is not None and hit[0] == version and now - hit[1] < CACHE_TTL_S:
            return hit[2]
    card = _risk(conn, ml, account_id=account_id, market=market, today=today, window=window)
    with _LOCK:
        for k in [k for k, (_, saved, _) in _CACHE.items() if now - saved >= CACHE_TTL_S]:
            del _CACHE[k]
        _CACHE[key] = (version, now, card)
    return card


def _risk(
    conn: sqlite3.Connection, ml: Any, *, account_id: str, market: str, today: date, window: int
) -> dict:
    """지금 보유 비중에 최근 `window` 거래일 수익을 적용한 하루 VaR·CVaR 95%(손실 비율), 유니버스
    1/N 대비 베타, 보유 상관, 종목별 위험 기여(분산 기여, 합 1). 표시 전용."""
    import numpy as np
    import pandas as pd

    snap = xledger.latest_positions(conn, account_id, market)
    if not snap:
        return {"insufficient": "잔고 스냅샷 없음"}
    nav = float(snap["nav"] or 0)
    held = {
        str(p["ticker"]): float(p["eval_amount"] or 0) / nav
        for p in snap["positions"]
        if nav > 0 and float(p["eval_amount"] or 0) > 0
    }
    if not held:
        return {"insufficient": "보유 없음(전액 현금)"}
    if ml is None:
        return {"insufficient": "종가 없음(ml 포트 없음)", "missing": sorted(held)}
    raw = ml.close_table(market, today - timedelta(days=window * 2 + 30), today)
    table = pd.DataFrame({t: pd.Series(col, dtype="float64") for t, col in raw.items()})
    table = table.sort_index()
    missing = sorted(t for t in held if t not in table.columns)
    rets = table.pct_change(fill_method=None).iloc[1:].tail(window) if not table.empty else table
    # 보유 종목별 유효 수익률이 MIN_OBS 미만이면 '종가 부족' — 0 으로 채우면 위험이 사라진다
    thin = [t for t in held if t not in missing and int(rets[t].notna().sum()) < MIN_OBS]
    missing = sorted(set(missing) | set(thin))
    have = [t for t in held if t not in missing]
    sub = rets[have].dropna() if have else rets.iloc[0:0]
    if not have or len(sub) < MIN_OBS:
        return {"insufficient": f"종가 부족({len(sub)}일)", "missing": missing}
    rets = rets.loc[sub.index]
    w = np.array([held[t] for t in have])
    port = sub.to_numpy() @ w
    q = float(np.quantile(port, 0.05))
    tail = port[port <= q]
    mkt = rets.mean(axis=1, skipna=True).fillna(0.0).to_numpy()  # 유니버스 1/N(지수 대용)
    var_m = float(np.var(mkt, ddof=1))
    beta = float(np.cov(port, mkt, ddof=1)[0, 1] / var_m) if var_m > 0 else None
    var_p = float(np.var(port, ddof=1))
    contributions = [
        {"ticker": t, "weight": round(held[t], 6),
         "contribution": w[i] * float(np.cov(sub[t].to_numpy(), port, ddof=1)[0, 1]) / var_p
         if var_p > 0 else 0.0}
        for i, t in enumerate(have)
    ]  # fmt: skip
    corr = sub.corr().fillna(0.0) if len(have) >= 2 else pd.DataFrame()
    return {
        "asof": pd.Timestamp(rets.index[-1]).date().isoformat(),
        "window": window,
        "n_obs": int(len(rets)),
        "var95": -q,
        "cvar95": -float(tail.mean()) if len(tail) else -q,
        "beta": beta,
        "corr": {
            "tickers": list(corr.columns) if not corr.empty else have,
            "matrix": corr.round(4).to_numpy().tolist() if not corr.empty else [],
        },
        "contributions": sorted(contributions, key=lambda x: -x["contribution"]),
        "missing": missing,
        "partial": bool(missing),  # 빠진 보유 종목이 있으면 그 비중만큼 덜 본 부분 추정
        "snapshot_date": snap["date"],
    }
