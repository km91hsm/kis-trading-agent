"""웹용 수집 현황: 크기 제한을 둔 메타데이터 조회, 원문·경로·예외는 반환하지 않는다."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trader.features.saveticker import automation
from trader.kernel.config import KST
from trader.kernel.platform.procs import pid_alive, same_process

MAX_JSON_BYTES = 1_048_576
MAX_STREAMS = 64
MAX_REVISIONS = 5000
STREAM_ID = re.compile(r"(?:news|community|market|calendar)(?:_[a-z0-9-]+)*\Z")
STATES = {
    "running",
    "collecting",
    "stopped",
    "paused",
    "blocked",
    "backoff",
    "error",
    "failed",
    "waiting_chrome",
    "waiting_verification",
    "auth_required",
    "logging_in",
    "reauthenticating",
}
ERRORS = {*automation.AUTH_ERRORS, "TimeoutError", "RuntimeError", "ConnectionError"}
REASONS = {*automation._REASON_TEXT, "extension_disconnected", "login_required", "access_denied"}


def _read(path: Path) -> tuple[dict, bool]:
    """없음은 정상 빈 값, 손상·크기 초과는 미확인으로 구분한다."""
    try:
        if path.is_symlink():
            return {}, True
        with path.open("rb") as source:
            raw = source.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            return {}, True
        value = json.loads(raw)
        return (value, False) if isinstance(value, dict) else ({}, True)
    except FileNotFoundError:
        return {}, False
    except (OSError, ValueError, RecursionError):
        return {}, True


def _stamp(value, now: datetime) -> str | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is not None and parsed <= now:
            return parsed.astimezone(UTC).isoformat()
    except ValueError:
        pass
    return None


def _known(value, allowed):
    return value if isinstance(value, str) and value in allowed else None


def _runtime(root: Path, now: datetime) -> tuple[dict, bool]:
    """기존 상태 응답 키를 유지하되 고정된 필드와 사유만 노출한다."""
    runtime = root / "runtime"
    files = {}
    invalid = False
    for name in (
        "bridge-config",
        "service",
        "bridge",
        "alert-monitor",
        "lane-blocked",
        *(f"runner-{lane}" for lane in automation.LANES),
    ):
        files[name], bad = _read(runtime / f"{name}.json")
        invalid |= bad
    backend = "extension" if files["bridge-config"].get("backend") == "extension" else "playwright"
    service = files["service"]
    health = _known(files["alert-monitor"].get("health"), {"healthy", "fault", "ok", "unknown"})
    alive = False
    if backend == "extension":
        bridge = files["bridge"]
        at = _stamp(bridge.get("updated_at"), now)
        lock, bad = _read(runtime / "bridge-host.lock")
        invalid |= bad
        pid = bridge.get("pid")
        holder = lock.get("pid")
        start = lock.get("proc_start")
        alive = bool(
            at
            and (now - datetime.fromisoformat(at)).total_seconds() <= 120
            and type(pid) is int
            and pid > 0
            and pid_alive(pid)
            and type(holder) is int
            and holder > 0
            and (start is None or isinstance(start, str))
            and same_process(holder, start)
        )
        service = (
            bridge
            if alive
            else {
                "state": "waiting_chrome",
                "reason": "extension_disconnected",
                "at": at,
            }
        )
        health = "healthy" if alive else "waiting_chrome"
    state = _known(service.get("state"), STATES)
    reason = _known(service.get("reason"), REASONS)
    blocked = state in {"blocked", "waiting_verification", "auth_required"}
    lanes = {}
    blocks = {}
    for lane in automation.LANES:
        row = files[f"runner-{lane}"]
        if backend == "extension" and row.get("backend") != "extension":
            row = {}
        lanes[lane] = {
            "state": "paused"
            if backend == "extension" and not alive
            else _known(row.get("state"), STATES),
            "error_type": _known(row.get("error_type"), ERRORS),
            "stage": _known(row.get("stage"), {"starting", "login", "collecting", "navigation"}),
            "updated_at": _stamp(row.get("updated_at"), now),
        }
        block = files["lane-blocked"].get(lane)
        if backend != "extension" and isinstance(block, dict):
            blocks[lane] = {
                "reason": _known(block.get("reason"), ERRORS),
                "at": _stamp(block.get("at"), now),
            }
    exit_code = service.get("exit_code")
    return {
        "backend": backend,
        "service": {
            "state": state,
            "reason": reason,
            "at": _stamp(service.get("at"), now),
            "exit_code": exit_code if type(exit_code) is int else None,
        },
        "blocked": blocked,
        "reason_text": automation._REASON_TEXT.get(reason, "수집이 중단되었습니다")
        if blocked
        else None,
        "lanes": lanes,
        "lane_blocks": blocks,
        "last_success_at": None,
        "health": "blocked" if backend == "extension" and blocked else health,
    }, invalid


def _history(root: Path, cutoffs: dict[str, str | None], now: datetime) -> tuple[dict, dict, bool]:
    """변경 이력 최근 5000건과 스트림별 최근 응답 50건에서 메타데이터만 조회한다.

    조회 범위 밖의 값 변화는 미기록으로 표시한다. Store 생성자는 DB·내보내기를 쓰므로 쓰지 않는다.
    """
    path = root / "data/feed.sqlite3"
    if not path.is_file() or path.is_symlink() or not cutoffs:
        return {}, {}, False
    changes, responses = {}, {}
    budget = 0

    def limit():
        nonlocal budget
        budget += 1
        return int(budget > 500)

    try:
        with closing(
            sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.1)
        ) as db:
            db.execute("PRAGMA query_only=ON")
            db.set_progress_handler(limit, 1000)
            for name, stamp in db.execute(
                "SELECT stream, received_at FROM revisions ORDER BY seq DESC LIMIT ?",
                (MAX_REVISIONS,),
            ):
                at = _stamp(stamp, now)
                cutoff = cutoffs.get(name)
                if cutoff and at and at <= cutoff and name not in changes:
                    changes[name] = at
            for name, cutoff in cutoffs.items():
                if not cutoff:
                    continue
                for stamp, status in db.execute(
                    "SELECT observed_at, CASE WHEN length(payload)<=? AND json_valid(payload) "
                    "THEN json_extract(payload,'$.status') END FROM "
                    "(SELECT observed_at,payload FROM captures WHERE stream=? "
                    "ORDER BY seq DESC LIMIT 50)",
                    (MAX_JSON_BYTES, name),
                ):
                    at = _stamp(stamp, now)
                    if at and at <= cutoff and status in ("ok", "partial"):
                        responses[name] = at
                        break
    except (OSError, sqlite3.Error):
        return {}, {}, True
    return changes, responses, False


def _label(name: str) -> str:
    if name == "news_all":
        return "뉴스 · 전체"
    if name == "news_reports":
        return "뉴스 · SAVE 리포트"
    if name == "community_top5":
        return "커뮤니티 · 인기 5종목"
    if name.startswith("calendar_"):
        return "캘린더 · " + name.removeprefix("calendar_")
    parts = name.split("_")
    if len(parts) == 3 and parts[0] in {"community", "market"}:
        group = "커뮤니티" if parts[0] == "community" else "증시"
        tab = {"index": "지수", "gain": "상승", "loss": "하락", "turnover": "거래대금"}.get(
            parts[1]
        )
        basis = {"live": "실시간", "regular": "정규장 기준"}.get(parts[2])
        if tab and basis:
            return f"{group} · {tab} · {basis}"
    return name


def _recovery(state: dict, now: datetime) -> dict:
    lanes = []
    for lane, block in state["lane_blocks"].items():
        at = block["at"]
        retry = (
            datetime.fromisoformat(at) + timedelta(seconds=automation.LANE_RETRY_S) if at else None
        )
        lanes.append(
            {
                "lane": lane,
                "state": "waiting" if retry and retry > now else "unknown",
                "retry_after_at": retry.isoformat() if retry else None,
                "text": "접근 제한으로 대기 중" if retry and retry > now else "재시도 결과 미확인",
            }
        )
    service = state["service"]["state"]
    if state["blocked"]:
        status, text = "manual", state["reason_text"]
    elif service == "backoff":
        status, text = "waiting", "오류 후 자동 재시도 대기 중"
    elif service == "waiting_chrome":
        status, text = "waiting", "Chrome에서 로그인과 확장 연결을 확인하세요"
    elif lanes:
        status, text = "partial", "접근이 제한된 작업은 재시도 대기 중입니다"
    else:
        status, text = "none", "복구 대기 없음"
    return {"state": status, "text": text, "lanes": lanes}


def _scope(name: str, now: datetime) -> str:
    """정본 감시 계약만 현재 집계에 쓴다. 확장의 실제 월별 계획은 파일에 기록되지 않는다."""
    if name in automation.STREAMS:
        return "active"
    month = name.removeprefix("calendar_")
    if name.startswith("calendar_") and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        if month < now.astimezone(KST).strftime("%Y-%m"):
            return "historical"
    return "unconfirmed"


def _runtime_state(state: dict, now: datetime, *, normal_count: int) -> str | None:
    """최근 저장 자료가 현재 중단·작업 오류를 가리지 않도록 한다."""
    service = state["service"]
    if state["blocked"]:
        return (
            "stopped"
            if service["reason"] in {"restart_limit", "chromium_install_failed"}
            else "login_required"
        )
    if service["state"] in {"stopped", "paused"}:
        return "stopped"
    if service["state"] in {"waiting_chrome", "backoff", "logging_in", "reauthenticating"}:
        return "waiting"
    if service["state"] in {"error", "failed"} or state["health"] == "fault":
        return "error"
    for lane in state["lanes"].values():
        at = lane["updated_at"]
        if at and (now - datetime.fromisoformat(at)).total_seconds() <= 120:
            if lane["state"] in {"error", "failed", "auth_required", "waiting_verification"}:
                return "partial" if normal_count else "error"
    return None


def read_status(root: Path, *, now: datetime) -> dict:
    state, invalid = _runtime(root, now)
    data, bad = _read(root / "data/status.json")
    raw = data.get("streams", {})
    bad |= not isinstance(raw, dict)
    raw = raw if isinstance(raw, dict) else {}
    names = sorted(n for n in raw if isinstance(n, str) and len(n) <= 80 and STREAM_ID.fullmatch(n))
    bad |= len(names) != len(raw) or len(names) > MAX_STREAMS
    names = names[:MAX_STREAMS]
    cutoffs = {
        name: _stamp(raw[name].get("last_attempt_at"), now) if isinstance(raw[name], dict) else None
        for name in names
    }
    changes, responses, history_bad = _history(root, cutoffs, now)
    recovery = _recovery(state, now)
    streams = []
    for name in names:
        row = raw[name] if isinstance(raw[name], dict) else {}
        attempt = _stamp(row.get("last_attempt_at"), now)
        success = _stamp(row.get("last_success_at"), now)
        status = _known(row.get("status"), {"ok", "partial", "error", "auth_required"})
        count = row.get("count")
        count = count if type(count) is int and 0 <= count <= 1_000_000 else None
        interval = row.get("target_interval_s")
        interval = interval if type(interval) in (int, float) and 2 <= interval <= 86400 else 30
        lane = (
            "background"
            if name.startswith("calendar") or name == "news_reports"
            else name.split("_")[0]
        )
        block = next((b for b in recovery["lanes"] if b["lane"] == lane), None)
        lane_row = state["lanes"].get(lane, {})
        lane_at = lane_row.get("updated_at")
        lane_current = bool(lane_at and attempt and lane_at >= attempt)
        error = None
        if (
            not attempt
            or not status
            or count is None
            or (row.get("last_success_at") and not success)
        ):
            result = "unknown"
            error = "수집 기록 또는 시각을 확인할 수 없습니다"
        elif status == "auth_required":
            result, error = "login_required", "로그인 또는 사이트 확인이 필요합니다"
        elif status == "error":
            result, error = "error", "최근 수집 요청에 오류가 있습니다"
        elif lane_current and lane_row.get("error_type") in automation.AUTH_ERRORS:
            result, error = "login_required", "로그인 또는 사이트 확인이 필요합니다"
        elif lane_current and lane_row.get("state") in {"error", "failed"}:
            result, error = "error", "최근 수집 작업에 오류가 있습니다"
        elif block:
            result = "stopped"
        elif (now - datetime.fromisoformat(attempt)).total_seconds() > max(120, interval * 3):
            result = "delayed"
        elif count == 0:
            result = "empty"
        elif status == "partial":
            result = "partial"
        else:
            result = "normal"
        response = attempt if status in {"ok", "partial"} else responses.get(name)
        change = changes.get(name)
        change_state = "unknown"
        if result == "empty":
            change_state = "empty"
        elif change and response and result in {"normal", "partial", "delayed"}:
            change_state = "changed" if change == attempt else "unchanged"
        streams.append(
            {
                "id": name,
                "label": _label(name),
                "scope": _scope(name, now),
                "state": result,
                "status": status,
                "last_attempt_at": attempt,
                "last_response_at": response,
                "last_success_at": success,
                "last_change_at": change,
                "change_state": change_state,
                "count": count,
                "error": error,
                "recovery": block["text"] if block else None,
            }
        )
    active = [s for s in streams if s["scope"] == "active"]
    counts = {
        name: sum(s["state"] == name for s in active)
        for name in (
            "normal",
            "partial",
            "empty",
            "delayed",
            "error",
            "waiting",
            "stopped",
            "login_required",
            "unknown",
        )
    }
    overall = (
        "normal"
        if active and counts["normal"] == len(active)
        else "partial"
        if active
        else "waiting"
    )
    if invalid or bad or counts["unknown"]:
        overall = "unknown"
    elif active and not counts["normal"]:
        overall = next(
            (
                s
                for s in ("login_required", "error", "stopped", "delayed", "empty", "partial")
                if counts[s]
            ),
            overall,
        )
    overall = _runtime_state(state, now, normal_count=counts["normal"]) or overall
    note = (
        "일부 기록을 읽을 수 없거나 조회 범위를 넘었습니다"
        if invalid or bad or history_bad
        else None
    )
    state["last_success_at"] = max(
        (s["last_success_at"] for s in streams if s["last_success_at"]), default=None
    )
    return {
        **state,
        "streams": streams,
        "summary": {
            "state": overall,
            "recorded": len(streams),
            "active_recorded": len(active),
            "historical": sum(s["scope"] == "historical" for s in streams),
            "unconfirmed": sum(s["scope"] == "unconfirmed" for s in streams),
            **counts,
            "note": note,
        },
        "recovery": recovery,
    }
