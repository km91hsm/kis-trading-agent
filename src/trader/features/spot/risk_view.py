"""spot 위험 카드(`risk_card`): 지금 보유 비중에 최근 거래일 수익을 적용한 하루 VaR·CVaR 95%·
베타·상관·위험 기여. 원장 스냅샷과 `ports["ml"].close_table` 만 읽는다(표시 전용). 옛
`ledger.risk_card` 이식 — `positions_view.py` 에서 나눴다(동작 불변)."""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import date, timedelta
from typing import Any

from trader.kernel.execution import ledger as xledger

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
