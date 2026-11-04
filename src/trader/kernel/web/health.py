"""시스템 헬스 판정(순수 함수): `evaluate(deps) -> {level, reasons, checked_at, version, features}`.

fail = 데몬 하트비트가 살아 있지 않음, warn = 같은 `runs.kind` 가 최근 24시간에 연속 3회 오류 ·
오늘 세션이 일일 상한의 90% 이상 · 하트비트 멈춤, info = control `ws_session_busy:*` 플래그.
M12 가 `kernel/ops` 로 바꿔도 응답 모양은 그대로 둔다."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from datetime import timedelta
from importlib import metadata
from typing import Any

from trader.kernel.daemon.heartbeat import read_status
from trader.kernel.session import usage

logger = logging.getLogger("trader.web")

ERROR_STREAK = 3
RUNS_WINDOW = timedelta(hours=24)
SESSION_WARN_RATIO = 0.9
BUSY_PREFIX = "ws_session_busy:"
_RANK = {"ok": 0, "info": 0, "warn": 1, "fail": 2}


def version() -> str:
    try:
        return metadata.version("trader")
    except metadata.PackageNotFoundError:
        return "0.2.0"


def _reason(area: str, level: str, text: str) -> dict:
    return {"area": area, "level": level, "text": text}


def _daemon(deps: Any, now) -> list[dict]:
    st = read_status(deps.state, now, [])
    if not st["alive"]:
        return [_reason("daemon", "fail", "데몬이 꺼져 있어요")]
    if st["stale"]:
        return [_reason("daemon", "warn", f"데몬 하트비트가 {st['age_s']}초째 멈춰 있어요")]
    return []


def _error_streaks(conn: sqlite3.Connection, now) -> list[dict]:
    since = (now - RUNS_WINDOW).isoformat()
    kinds = [
        r["kind"]
        for r in conn.execute(
            "SELECT DISTINCT kind FROM runs WHERE started_at >= ? ORDER BY kind", (since,)
        )
    ]
    out = []
    for kind in kinds:
        rows = conn.execute(
            "SELECT status, error FROM runs WHERE kind=? AND started_at >= ? "
            "ORDER BY started_at DESC, id DESC LIMIT ?",
            (kind, since, ERROR_STREAK),
        ).fetchall()
        if len(rows) == ERROR_STREAK and all(r["status"] == "error" for r in rows):
            last = (rows[0]["error"] or "").strip()[:120]
            text = f"{kind} 최근 {ERROR_STREAK}회 연속 오류" + (f" — {last}" if last else "")
            out.append(_reason("runs", "warn", text))
    return out


def _sessions(conn: sqlite3.Connection, settings: Any, now) -> list[dict]:
    cap = getattr(getattr(settings, "llm", None), "daily_session_cap", None)
    if not cap:
        return []
    used = usage.ledger_windows(conn, now, cap=cap)["today"]["sessions"]
    if used / cap >= SESSION_WARN_RATIO:
        return [_reason("sessions", "warn", f"오늘 세션 {used}/{cap} — 일일 상한에 가까워요")]
    return []


def _busy_flags(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT key FROM control WHERE key LIKE ? ORDER BY key", (BUSY_PREFIX + "%",)
    ).fetchall()
    return [_reason("control", "info", r["key"]) for r in rows]


def evaluate(deps: Any) -> dict:
    """하트비트·원장을 읽어 단계와 사유를 낸다. 원장을 못 읽으면 그 사유만 warn 으로 더한다."""
    now = deps.now()
    reasons = _daemon(deps, now)
    try:
        with closing(deps.conn_factory()) as conn:
            reasons += _error_streaks(conn, now)
            reasons += _sessions(conn, deps.settings, now)
            reasons += _busy_flags(conn)
    except sqlite3.Error as e:
        logger.warning("헬스 원장 조회 실패: %s", e)
        reasons.append(_reason("ledger", "warn", f"원장 조회 실패: {e}"))
    worst = max((_RANK[r["level"]] for r in reasons), default=0)
    return {
        "level": ("ok", "warn", "fail")[worst],
        "reasons": reasons,
        "checked_at": now.isoformat(),
        "version": version(),
        "features": [f.id for f in deps.features],
    }
