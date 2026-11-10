"""원장 상태를 고치지 않는 작업 이력 분류. 후속 정상 실행은 원인 해결을 보장하지 않는다."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Literal

Source = Literal["runs", "job_requests"]
_STOP_MESSAGES = (
    "세션 중단 (종료 요청)",
    "데몬 종료 요청으로 중단",
    "데몬 종료 중",
    "종료 중",
    "JobCancelled: 종료 중",
)


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def _object(value: object) -> dict | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) else None


def _scope(row: dict, source: Source) -> str | None:
    """요청의 전체 params 또는 실행 결과에 명시된 시장·종목·계좌를 포함한 전체 params만 쓴다.

    runs의 market 하나나 agent_calls의 ticker만으로 계좌·요청 범위를 추정하지 않는다.
    """
    if source == "job_requests":
        params = _object(row.get("params"))
    else:
        result = _object(row.get("result_json"))
        params = _object(result.get("params")) if result is not None else None
        if params is not None and (
            not all(isinstance(params.get(k), str) and params[k] for k in
                    ("market", "ticker", "account_id"))
            or params["market"] != row.get("market")
        ):
            return None
    return json.dumps(params, sort_keys=True, ensure_ascii=False) if params is not None else None


def outcome_for(status: str | None, error: str | None, result: object = None) -> str | None:
    """기록된 상태·정확한 종료 사유만 분류한다. 후속 정상 연결은 별도 범위 검사가 필요하다."""
    data = _object(result)
    if status in {"error", "skipped"} and (
        error in _STOP_MESSAGES or (data is not None and data.get("skipped") in _STOP_MESSAGES)
    ):
        return "interrupted"
    return "failed" if status == "error" else status


def _outcome(row: dict, source: Source) -> str | None:
    return outcome_for(
        row.get("status"), row.get("error"),
        row.get("result_json" if source == "runs" else "result"),
    )


def annotate(conn: sqlite3.Connection, rows: list[dict], *, source: Source) -> list[dict]:
    """표시 대상 밖의 후속 성공도 조회한다. 원시 status/error와 전달받은 행은 보존한다."""
    failed = [r for r in rows if _outcome(r, source) == "failed"]
    bounds = [t for r in failed if (t := _time(r.get("finished_at"))) is not None]
    successes: dict[tuple[str, str], list[dict]] = {}
    if bounds:
        # source는 외부 입력이 아니라 이 모듈이 정한 테이블 이름이다.
        if source not in {"runs", "job_requests"}:
            raise ValueError("알 수 없는 작업 이력")
        kinds = sorted({r["kind"] for r in failed})
        candidates = conn.execute(
            f"SELECT * FROM {source} WHERE status='ok' "
            f"AND kind IN ({','.join('?' for _ in kinds)}) "
            "AND julianday(started_at) > julianday(?)",
            [*kinds, min(bounds).isoformat()],
        )
        for candidate in candidates:
            success = dict(candidate)
            scope = _scope(success, source)
            if scope is not None:
                successes.setdefault((success["kind"], scope), []).append(success)
    out = []
    for row in rows:
        scope = _scope(row, source)
        outcome = _outcome(row, source)
        followup_at = None
        if outcome == "failed":
            end = _time(row.get("finished_at"))
            matched = []
            if scope is not None and end is not None:
                for candidate in successes.get((row["kind"], scope), []):
                    start = _time(candidate.get("started_at"))
                    finish = _time(candidate.get("finished_at"))
                    if start is not None and finish is not None and end < start <= finish:
                        matched.append((finish, candidate["finished_at"]))
            if matched:
                outcome, followup_at = "followed_by_success", min(matched)[1]
        out.append({**row, "outcome": outcome, "followup_at": followup_at,
                    "scope_confirmed": scope is not None})
    return out


def recent_errors(conn: sqlite3.Connection, now: datetime) -> dict[str, int]:
    """최근 24시간에 시작한 error 행 내역. 중단도 이력 건수에 남긴다."""
    since = (now - timedelta(hours=24)).isoformat()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM runs WHERE status='error' "
        "AND julianday(started_at) >= julianday(?) AND julianday(started_at) <= julianday(?)",
        (since, now.isoformat()),
    )]
    counts = {"total": len(rows), "failed": 0, "interrupted": 0, "followed_by_success": 0}
    for row in annotate(conn, rows, source="runs"):
        counts[row["outcome"]] += 1
    return counts


def snapshot(conn: sqlite3.Connection, now: datetime) -> dict:
    """실행 단계와 요청은 중첩될 수 있으므로 개수를 합치지 않는다."""
    running = [dict(r) for r in conn.execute(
        "SELECT * FROM runs WHERE status='running' ORDER BY started_at, id"
    )]
    counts = {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) AS n FROM job_requests "
        "WHERE status IN ('queued','running') GROUP BY status"
    )}
    return {
        "checked_at": now.isoformat(),
        "since": (now - timedelta(hours=24)).isoformat(),
        "running_runs": annotate(conn, running, source="runs"),
        "queued_requests": counts.get("queued", 0),
        "running_requests": counts.get("running", 0),
        "recent_errors": recent_errors(conn, now),
    }
