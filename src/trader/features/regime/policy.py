"""현물 매수 수동 정책. AI 판단은 보존하고 control·이력만 원자적으로 쓴다."""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, time, timedelta
from typing import Any

from trader.features.regime import market_view, regime
from trader.kernel.config import KST
from trader.kernel.config.time import MARKET_TZ
from trader.kernel.ledger import control
from trader.kernel.session.run_store import run_date_for


def load(conn: sqlite3.Connection, market: str, now: datetime) -> dict:
    """만료·깨진 정책은 쓰기 없이 AUTO로 복귀한다. US 운영일은 장중 KST 자정을 넘는다."""
    auto = {"mode": "AUTO", "source": "auto", "reason": None, "expires_at": None, "date": None}
    try:
        raw = control.get_flag(conn, f"risk_policy:{market}")
        if not isinstance(raw, dict) or raw.get("mode") not in ("RISK_ON", "RISK_OFF"):
            return auto
        expiry = datetime.fromisoformat(raw["expires_at"])
        if (expiry.tzinfo is None or now >= expiry
                or raw.get("date") != run_date_for(market, now).isoformat()
                or not isinstance(raw.get("reason"), str) or not raw["reason"].strip()):
            return auto
        return {k: raw.get(k) for k in auto} | {"source": "manual"}
    except (KeyError, ValueError, TypeError):
        return auto


def save(conn: sqlite3.Connection, market: str, mode: str, reason: str, *, now: datetime,
         actor: str) -> None:
    """control과 control_log를 한 트랜잭션에서 함께 기록한다."""
    day = run_date_for(market, now)
    expiry = datetime.combine(day + timedelta(days=1), time(),
                              tzinfo=MARKET_TZ.get(market, KST)).astimezone(UTC)
    new = {"mode": mode, "source": "auto" if mode == "AUTO" else "manual",
           "reason": reason, "date": day.isoformat(),
           "expires_at": None if mode == "AUTO" else expiry.isoformat()}
    key = f"risk_policy:{market}"
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        old = conn.execute("SELECT value FROM control WHERE key = ?", (key,)).fetchone()
        conn.execute(
            "INSERT INTO control(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) "
            "DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (key, json.dumps(new, ensure_ascii=False), now.astimezone(UTC).isoformat()),
        )
        conn.execute("INSERT INTO control_log(at,channel,actor,key,old,new) VALUES(?,?,?,?,?,?)",
                     (now.astimezone(UTC).isoformat(), "web", actor, key,
                      old["value"] if old else None, json.dumps(new, ensure_ascii=False)))


def context(conn: sqlite3.Connection, market: str, now: datetime, settings: Any) -> dict:
    """분석·실행·화면이 공유하는 정책과 시장 단위 상한. 계좌별 보호 규칙은 추가로 적용한다."""
    policy = load(conn, market, now)
    state = regime.load(conn, market) or {}
    ai = market_view.load_view(conn, market, run_date_for(market, now))
    risk, hedge = settings.risk, settings.hedge
    mode = policy["mode"]
    pct, n = risk.max_position_pct, risk.max_positions
    entry, stance = "standard", "RISK_ON"
    reason = "자동 정책: 기존 국면·AI 판단 및 계좌별 위험 규칙 적용"
    if mode == "RISK_OFF":
        entry, stance = "blocked", "RISK_OFF"
        reason = "수동 RISK_OFF: 일반 현물 신규·추가 매수 중단"
    elif mode == "RISK_ON":
        pct, n = min(pct, 0.1), min(n, 2)
        entry, reason = "limited", "수동 RISK_ON: 종목당 최대 10%·2종목, 기존 위험 규칙 적용"
    elif state.get("state") not in ("bull", "neutral", "bear"):
        entry, stance, pct, n = "unknown", "UNKNOWN", None, None
        reason = "국면 기록 없음: 자동 정책의 시장 상태를 확인할 수 없습니다"
    elif hedge.enabled:
        tilt = 0
        if hedge.agent_gate and ai:
            try:
                if float(ai.get("confidence") or 0) >= hedge.agent_min_confidence:
                    tilt = {"RISK_ON": 1, "RISK_OFF": -1}.get(ai.get("stance"), 0)
            except (TypeError, ValueError):
                pass
        st = state["state"]
        cap = None
        if st == "bear":
            if tilt > 0:
                cap = (0.1, 2)
            else:
                entry, stance, reason = "blocked", "RISK_OFF", "국면 bear: 일반 현물 신규 매수 중단"
        elif st == "neutral":
            cap = (0.2, 4) if tilt > 0 else (0.1, 2) if tilt < 0 else (0.15, 3)
        elif tilt < 0:
            cap = (0.15, 3)
        if cap:
            pct, n = min(pct, cap[0]), min(n, cap[1])
            entry, stance = "limited", "NEUTRAL"
    return {"policy": policy, "effective": {"stance": stance, "entry": entry, "reason": reason,
            "max_position_pct": pct, "max_positions": n}}


def history(conn: sqlite3.Connection, market: str) -> list[dict]:
    """현재 정책 만료와 무관하게 최근 사용자 조작을 보여준다."""
    out = []
    for row in conn.execute("SELECT at,actor,new FROM control_log WHERE key=? "
                            "ORDER BY id DESC LIMIT 20", (f"risk_policy:{market}",)):
        try:
            new = json.loads(row["new"])
            out.append({"at": row["at"], "mode": new["mode"], "reason": new.get("reason"),
                        "actor": row["actor"], "expires_at": new.get("expires_at")})
        except (ValueError, TypeError, KeyError):
            continue
    return out
