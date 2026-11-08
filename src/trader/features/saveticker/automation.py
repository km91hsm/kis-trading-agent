"""Chrome 확장 수집의 로컬 장애 감시와 상태 요약(AI 호출 없음).

수집은 일반 Chrome 의 확장 + Native host(`native.py`)가 한다. 이 모듈은 Native host 가 남긴
`runtime/*.json` 과 `data/status.json` 만 읽어 판정한다 — 데몬 잡(`job.watch`)이 `monitor` 로
장애·복구를 텔레그램에 한 번씩 알리고, 웹·운영 점검·`extension status` 가 `status` 를 쓴다."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime

from trader.features.saveticker.bridge_install import EXTENSION_ID
from trader.features.saveticker.store import atomic_json
from trader.kernel.config import KST

RUNNERS = ("runner", "runner-background", "runner-community", "runner-market", "runner-news")
STREAMS = (
    "news_all",
    "community_index_live",
    "community_gain_live",
    "community_loss_live",
    "community_top5",
    "market_turnover_live",
    "market_gain_live",
    "market_loss_live",
)
LANES = ("background", "community", "market", "news")
BLOCKED_STATES = ("waiting_verification", "auth_required")


def read(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def registered(home) -> bool:
    """Native host 가 등록돼 확장 수집을 쓰는 중인지(`extension install` 이 남긴 설정)."""
    return (read(home / "runtime" / "bridge-config.json") or {}).get("backend") == "extension"


def collector_alive(pid):
    """pid 가 살아 있는 Native host(확장이 띄운 `trader saveticker native-host`)인지."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":  # ps 가 없다 — pid 생존만 본다
        from trader.kernel.platform import procs

        return procs.pid_alive(pid)
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=5,
    )
    return result.returncode == 0 and (
        "trader saveticker native-host" in result.stdout
        or ("trader" in result.stdout and f"chrome-extension://{EXTENSION_ID}/" in result.stdout)
    )


def evaluate(runners, status, previous, *, now, alive=collector_alive):
    counts = dict(previous.get("failure_counts", {}))
    startup = dict(previous.get("startup", {}))
    faults = []
    last = dict(previous.get("last_success_at", {}))
    for name in RUNNERS:
        row = runners.get(name)
        key = "file:" + name
        counts[key] = counts.get(key, 0) + 1 if row is None else 0
        if row is None:
            if counts[key] >= 2:
                faults.append(key)
            continue
        if not alive(row.get("pid")):
            faults.append("process:" + name)
        elif row.get("state") in ("error", "stopped") or row.get("exit_code") is not None:
            faults.append("state:" + name)
        if startup.get(name, {}).get("pid") != row.get("pid"):
            startup[name] = {"pid": row.get("pid"), "first_seen_at": now.isoformat()}
    counts["file:status"] = counts.get("file:status", 0) + 1 if status is None else 0
    if counts["file:status"] >= 2:
        faults.append("file:status")
    for name in STREAMS:
        row = (status or {}).get("streams", {}).get(name, {})
        stamp = row.get("last_success_at")
        if stamp:
            last[name] = stamp
        try:
            stale = not stamp or (now - datetime.fromisoformat(stamp)).total_seconds() > 120
        except (ValueError, TypeError):
            stale = True
        lane = (
            "news"
            if name.startswith("news")
            else "community"
            if name.startswith("community")
            else "market"
        )
        boot = startup.get("runner-" + lane, {}).get("first_seen_at")
        # 첫 수집 전(유효 자료 없음)에만 새 PID 기동 유예를 준다 — 이미 자료가 있으면 지연을
        # 숨기지 않는다.
        grace = bool(boot and (now - datetime.fromisoformat(boot)).total_seconds() < 180)
        grace = grace and not stamp
        bad = stale or row.get("status") in ("error", "auth_required")
        key = "stale:" + name
        counts[key] = counts.get(key, 0) + 1 if bad and not grace else 0
        if counts[key] >= 2:
            faults.append(key)
    roots = [f for f in faults if not f.startswith("stale:")]
    faults = sorted(roots or faults)
    return {
        "checked_at": now.isoformat(),
        "health": "fault" if faults else "healthy",
        "faults": faults,
        "failure_counts": {k: v for k, v in counts.items() if v},
        "first_detected_at": (previous.get("first_detected_at") or now.isoformat())
        if faults
        else None,
        "startup": startup,
        "last_success_at": last,
        "last_notified_fingerprint": previous.get("last_notified_fingerprint", ""),
    }


def monitor(root, *, send):
    """장애·복구를 판정해 바뀌었을 때만 `send(message) -> bool` 로 한 번 알린다(데몬은 텔레그램).
    메시지는 고정 문구·스트림 이름·시각만 포함한다."""
    from trader.kernel.daemon.lock import Lock

    runtime = root / "runtime"
    lock = Lock(runtime / "alert-monitor.lock")
    if not lock.acquire():
        return {"notification": "busy"}
    try:
        previous = read(runtime / "alert-monitor.json") or {}
        runners = {n: read(runtime / (n + ".json")) for n in RUNNERS}
        state = evaluate(runners, read(root / "data/status.json"), previous, now=datetime.now(UTC))
        fingerprint = "|".join(state["faults"])
        old = previous.get("last_notified_fingerprint", "")
        changed = fingerprint != old
        state["notification"] = "unchanged"
        if changed:
            stamp = datetime.now(KST).strftime("%m/%d %H:%M KST")
            last = min((v for v in state["last_success_at"].values() if v), default=None)
            last = (
                datetime.fromisoformat(last).astimezone(KST).strftime("%m/%d %H:%M KST")
                if last
                else "미확인"
            )
            summary = (
                "수집 장애: " + ", ".join(state["faults"]) if fingerprint else "수집 복구 확인"
            )
            message = f"{stamp} · {summary} · 마지막 정상 {last}"
            try:
                delivered = send(message)
            except (OSError, subprocess.SubprocessError):
                delivered = False
            state["notification"] = "submitted" if delivered else "failed"
            if delivered:
                state["last_notified_fingerprint"] = fingerprint
        atomic_json(runtime / "alert-monitor.json", state)
        return {
            "health": state["health"],
            "faults": state["faults"],
            "notification": state["notification"],
        }
    finally:
        lock.release()


REASON_TEXT = {
    "verification_required": "사이트 재확인 필요 — Chrome에서 확인 후 확장 팝업에서 재개하세요",
    "login_required": "로그인 필요 — Chrome에서 세이브티커에 로그인한 뒤 확장 팝업에서 재개하세요",
    "access_denied": "접근 거부 — Chrome에서 사이트를 확인한 뒤 확장 팝업에서 재개하세요",
}


DIAGNOSTIC_CATEGORIES = {"recheck", "html_trial"}


def _not_before(value, since) -> bool:
    """`value` 시각이 `since` 이상인가(둘 다 시간대가 있는 ISO 시각일 때만 참)."""
    try:
        a, b = datetime.fromisoformat(value), datetime.fromisoformat(since)
        return a.tzinfo is not None and b.tzinfo is not None and a >= b
    except (TypeError, ValueError):
        return False


def diagnostic(runtime, reader=None, *, blocked_at=None) -> dict | None:
    """확장의 마지막 진단(`runtime/extension-diagnostics.json`, Native host 가 허용 목록으로 검증해
    저장). `terminal` 이 False 면 계속 복구가 재시도를 기다리는 중이라 「복구 재시도 대기」로
    보인다. 키가 없는 옛 기록은 중지(True)로 본다. 차단 중이면(`blocked_at` = 지금 차단 시각)
    진단이 그 시각 이상일 때만 재시도 대기(`retrying`) — 더 오래된 진단은 지난 차단의 것이다."""
    row = (reader or read)(runtime / "extension-diagnostics.json")
    if not isinstance(row, dict):
        return None
    category, reason = row.get("category"), row.get("reason")
    if category not in DIAGNOSTIC_CATEGORIES or not isinstance(reason, str) or len(reason) > 40:
        return None
    terminal = row.get("terminal", True) is not False
    at = row.get("recorded_at")
    at = at if isinstance(at, str) and len(at) <= 40 else None
    retrying = not terminal and (blocked_at is None or _not_before(at, blocked_at))
    return {
        "category": category,
        "reason": reason,
        "terminal": terminal,
        "retrying": retrying,
        "recorded_at": at,
        "text": "복구 재시도 대기 · 확장이 간격을 두고 다시 시도합니다"
        if retrying
        else "자동 중지 · 직접 확인 필요"
        if terminal
        else "직접 확인 필요",
    }


def status(home):
    """웹·운영 점검용 요약: 확장 등록 여부·Native host 연결 상태·멈춘 이유·작업별 상태·마지막
    수집 시각. Native host 가 2분 안에 기록하지 않았거나 잠금을 쥐지 않으면 `waiting_chrome`."""
    from trader.kernel.daemon.lock import Lock
    from trader.kernel.platform.procs import pid_alive

    runtime = home / "runtime"
    bridge = read(runtime / "bridge.json") or {}
    try:
        at = datetime.fromisoformat(bridge["updated_at"])
        fresh = 0 <= (datetime.now(UTC) - at).total_seconds() <= 120
    except (KeyError, ValueError, TypeError):
        fresh = False
    alive = fresh and pid_alive(bridge.get("pid", 0)) and Lock(runtime / "bridge-host.lock").held()
    service_state = (
        bridge
        if alive
        else {
            "state": "waiting_chrome",
            "reason": "extension_disconnected",
            "at": bridge.get("updated_at"),
        }
    )
    lanes = {}
    for lane in LANES:
        row = read(runtime / f"runner-{lane}.json") or {}
        if row.get("backend") != "extension":
            row = {}
        lanes[lane] = {k: row.get(k) for k in ("state", "error_type", "stage", "updated_at")}
        if not alive:
            lanes[lane]["state"] = "paused"
            lanes[lane]["error_type"] = None
    data_status = read(home / "data/status.json") or {}
    streams = data_status.get("streams")
    stamps = (
        [v.get("last_success_at") for v in streams.values() if isinstance(v, dict)]
        if isinstance(streams, dict)
        else []
    )
    blocked = service_state.get("state") in BLOCKED_STATES
    reason = service_state.get("reason")
    return {
        "registered": registered(home),
        "service": {k: service_state.get(k) for k in ("state", "reason", "at", "exit_code")},
        "blocked": blocked,
        "reason_text": REASON_TEXT.get(reason, reason) if blocked else None,
        "lanes": lanes,
        "last_success_at": max((v for v in stamps if isinstance(v, str)), default=None),
        "health": "blocked" if blocked else "healthy" if alive else "waiting_chrome",
        "diagnostic": diagnostic(
            runtime, blocked_at=(service_state.get("at") or "") if blocked else None
        ),
    }
