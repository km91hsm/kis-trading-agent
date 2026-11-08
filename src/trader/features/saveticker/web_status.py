"""웹용 수집 현황: 크기 제한을 둔 메타데이터 조회, 원문·경로·예외는 반환하지 않는다."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from trader.features.saveticker import api_contract, automation
from trader.kernel.config import KST
from trader.kernel.platform.procs import pid_alive, same_process

MAX_JSON_BYTES = 1_048_576
MAX_STREAMS = 64
MAX_REVISIONS = 5000
STREAM_ID = re.compile(r"(?:news|community|market|calendar)(?:_[a-z0-9-]+)*\Z")
# Native host 가 기록하는 상태(`native.STATES`) + 연결이 끊겼을 때 이 모듈이 붙이는 `waiting_chrome`
STATES = {
    "connecting",
    "collecting",
    "paused",
    "error",
    "waiting_chrome",
    "waiting_verification",
    "auth_required",
}
AUTH_ERRORS = {"AccessDenied", "LoginRequired", "VerificationRequired"}
ERRORS = {*AUTH_ERRORS, "TimeoutError", "RuntimeError", "ConnectionError"}
REASONS = {*automation.REASON_TEXT, "extension_disconnected"}
# 응답 거절 코드(`api_contract.API_ERRORS`)의 한국어 사유 — 확장 팝업(popup.js `errors`)과 같은 뜻.
FAILURE_TEXT = {
    "api_contract_unverified": "이 항목의 API 요청 조건이 아직 확인되지 않았습니다",
    "api_response_unverified": "응답 형식·상태를 확인하지 못했습니다",
    "api_shape_unverified": "데이터 구조를 확인하지 못했습니다",
    "api_context_unverified": "기본 그룹을 확인하지 못했습니다",
    "api_detail_unverified": "본문 응답을 확인하지 못했습니다",
    "api_source_unverified": "출처를 확인하지 못했습니다",
    "api_basis_unverified": "실시간·정규장 기준을 확인하지 못했습니다",
    "api_regular_unverified": "정규장 기준을 확인하지 못했습니다",
    "api_pdf_unverified": "PDF 응답을 확인하지 못했습니다",
    "api_response_too_large": "응답 크기 제한을 넘었습니다",
    "api_busy": "이전 응답 처리를 기다립니다",
    "api_content_unavailable": "전용 탭 응답이 없습니다",
    "api_already_prepared": "이미 준비된 작업입니다",
    "invalid_pdf_path": "PDF 저장 경로를 확인하지 못했습니다",
    "invalid_pdf_ticket": "PDF 저장 요청이 만료됐거나 일치하지 않습니다",
    "invalid_pdf": "유효한 PDF 파일이 아닙니다",
    "invalid_job": "작업이 만료됐거나 일치하지 않습니다",
    "invalid_message": "호스트 메시지를 확인하지 못했습니다",
    "lane_busy": "이전 수집 작업의 종료를 기다립니다",
    "snapshot_too_large": "저장 크기 제한을 넘었습니다",
    "unknown_stream": "수집 항목을 확인하지 못했습니다",
}


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
    """Native host 기록에서 고정된 필드와 사유만 노출한다. 2분 안에 기록이 없거나 호스트 잠금이
    다른 프로세스면 `waiting_chrome`."""
    runtime = root / "runtime"
    files = {}
    invalid = False
    for name in ("bridge", *(f"runner-{lane}" for lane in automation.LANES)):
        files[name], bad = _read(runtime / f"{name}.json")
        invalid |= bad
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
        else {"state": "waiting_chrome", "reason": "extension_disconnected", "at": at}
    )
    state = _known(service.get("state"), STATES)
    reason = _known(service.get("reason"), REASONS)
    blocked = state in set(automation.BLOCKED_STATES)
    lanes = {}
    for lane in automation.LANES:
        row = files[f"runner-{lane}"]
        if row.get("backend") != "extension":
            row = {}
        lanes[lane] = {
            "state": "paused" if not alive else _known(row.get("state"), STATES),
            "error_type": _known(row.get("error_type"), ERRORS),
            "stage": _known(row.get("stage"), {"starting", "login", "collecting", "navigation"}),
            "updated_at": _stamp(row.get("updated_at"), now),
        }
    exit_code = service.get("exit_code")
    return {
        "service": {
            "state": state,
            "reason": reason,
            "at": _stamp(service.get("at"), now),
            "exit_code": exit_code if type(exit_code) is int else None,
        },
        "blocked": blocked,
        "reason_text": automation.REASON_TEXT.get(reason, "수집이 중단되었습니다")
        if blocked
        else None,
        "lanes": lanes,
        "last_success_at": None,
        "health": "blocked" if blocked else "healthy" if alive else "waiting_chrome",
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


def _lane(name: str) -> str:
    return (
        "background"
        if name.startswith("calendar") or name == "news_reports"
        else name.split("_")[0]
    )


def _recovery(state: dict, root: Path) -> dict:
    service = state["service"]["state"]
    if state["blocked"]:
        last = automation.diagnostic(root / "runtime", reader=lambda p: _read(p)[0])
        if last and last["category"] == "recheck" and not last["terminal"]:
            return {"state": "retrying", "text": last["text"]}
        return {"state": "manual", "text": state["reason_text"]}
    if service == "waiting_chrome":
        return {"state": "waiting", "text": "Chrome에서 로그인과 확장 연결을 확인하세요"}
    return {"state": "none", "text": "복구 대기 없음"}


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
        return "login_required"
    if service["state"] == "paused":
        return "stopped"
    if service["state"] in {"waiting_chrome", "connecting"}:
        return "waiting"
    if service["state"] == "error":
        return "error"
    for lane in state["lanes"].values():
        at = lane["updated_at"]
        if at and (now - datetime.fromisoformat(at)).total_seconds() <= 120:
            if lane["state"] in {"error", "auth_required", "waiting_verification"}:
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
    recovery = _recovery(state, root)
    streams = []
    for name in names:
        row = raw[name] if isinstance(raw[name], dict) else {}
        attempt = _stamp(row.get("last_attempt_at"), now)
        success = _stamp(row.get("last_success_at"), now)
        status = _known(row.get("status"), {"ok", "partial", "error", "auth_required"})
        count = row.get("count")
        count = count if type(count) is int and 0 <= count <= 1_000_000 else None
        # 응답 거절로 끝난 최근 시도(`Store.record_failure`): 최근 성공보다 새 시도일 때만 보인다.
        code = row.get("last_error")
        failed = (
            code in api_contract.API_ERRORS
            and attempt is not None
            and (success is None or attempt > success)
        )
        last_error = code if failed else None
        # 지연은 지금처럼 성공(저장된 응답) 시각 기준 — 실패 시도가 지연을 가리지 않게.
        delay_basis = success if failed and success else attempt
        interval = row.get("target_interval_s")
        interval = interval if type(interval) in (int, float) and 2 <= interval <= 86400 else 30
        lane = _lane(name)
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
        elif lane_current and lane_row.get("error_type") in AUTH_ERRORS:
            result, error = "login_required", "로그인 또는 사이트 확인이 필요합니다"
        elif lane_current and lane_row.get("state") == "error":
            result, error = "error", "최근 수집 작업에 오류가 있습니다"
        elif (now - datetime.fromisoformat(delay_basis)).total_seconds() > max(120, interval * 3):
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
                "last_error": last_error,
                "last_error_text": FAILURE_TEXT.get(last_error) if last_error else None,
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
