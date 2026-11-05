"""보존 정리: 오래된 실행 산출물·운영일 플래그·잡 기록을 지운다(내장 cron 잡 `retention`).

지우는 것: `state/runs/<날짜>` 디렉터리, 키에 날짜가 든 control 행(운영일 플래그), 끝난
`job_requests`, 세션이 없는 `runs`(세션 행이 있는 runs 는 운영일 집계라 남긴다),
`llm_rate_limits`, 알림 기록 `notices`, 제어 변경 기록 `control_log`.
결정·주문·체결·메모리 같은 원장 사실은 지우지 않는다.
"""

from __future__ import annotations

import logging
import re
import shutil
import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path

from trader.kernel.config import KST
from trader.kernel.platform import paths

logger = logging.getLogger("trader.ops")

_DATE_SEGMENT = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 날짜가 들어 있어도 지우지 않는 영구 키(계좌 id 에 날짜 모양이 섞여도 안전하게).
# 계좌 모드·실전 확인은 control 키가 아니라 `account_modes` 테이블이다.
PERSISTENT_PREFIXES: frozenset[str] = frozenset(
    {"paused", "watchlist", "telegram_offset", "memory_breaker"}
)
# (테이블, 시각 열) — 시각이 cutoff 보다 이르면 지우는 기록 테이블
_TIMED_TABLES: tuple[tuple[str, str], ...] = (
    ("llm_rate_limits", "observed_at"),
    ("notices", "at"),
    ("control_log", "at"),
)


def old_date(key: str, cutoff: date) -> bool:
    """`:` 로 나눈 **세그먼트 하나가 통째로 날짜**인 운영일 플래그만 대상. 운영일은 마지막 날짜
    세그먼트다(계좌 id 가 날짜 모양이어도 앞쪽에 온다: `sl_unrecorded:<계좌>:<시장>:<종목>:<날짜>`).
    첫 세그먼트가 영구 접두면 지우지 않는다."""
    parts = key.split(":")
    if parts[0] in PERSISTENT_PREFIXES:
        return False
    dates = [seg for seg in parts[1:] if _DATE_SEGMENT.match(seg)]
    if not dates:
        return False
    try:
        return date.fromisoformat(dates[-1]) < cutoff
    except ValueError:
        return False


def prune(
    conn: sqlite3.Connection, state: Path | None, *, today: date, keep_days: int
) -> dict[str, int]:
    """keep_days 보다 오래된 것을 지우고 종류별 건수를 돌려준다(멱등)."""
    cutoff = today - timedelta(days=keep_days)
    cutoff_iso = datetime.combine(cutoff, time.min, KST).isoformat()
    out = {"run_dirs": 0, "control": 0, "job_requests": 0, "runs": 0}

    root = (state or paths.state_dir()) / "runs"
    if root.exists():
        for d in sorted(root.iterdir()):
            try:
                day = date.fromisoformat(d.name)
            except ValueError:
                continue
            if d.is_dir() and day < cutoff:
                shutil.rmtree(d, ignore_errors=True)
                out["run_dirs"] += 1

    keys = [r[0] for r in conn.execute("SELECT key FROM control").fetchall()]
    old = [k for k in keys if old_date(k, cutoff)]
    conn.executemany("DELETE FROM control WHERE key=?", [(k,) for k in old])
    out["control"] = len(old)
    out["job_requests"] = conn.execute(
        "DELETE FROM job_requests WHERE status NOT IN ('queued','running') AND requested_at < ?",
        (cutoff_iso,),
    ).rowcount
    out["runs"] = conn.execute(
        "DELETE FROM runs WHERE started_at < ? AND NOT EXISTS ("
        "SELECT 1 FROM agent_calls a WHERE a.run_id = runs.id)",
        (cutoff_iso,),
    ).rowcount
    for table, column in _TIMED_TABLES:  # 이름은 위 상수뿐(사용자 입력 아님)
        out[table] = conn.execute(
            f"DELETE FROM {table} WHERE {column} < ?", (cutoff_iso,)
        ).rowcount
    conn.commit()
    if any(out.values()):
        logger.info("보존 정리(%d일 이전): %s", keep_days, out)
    return out
