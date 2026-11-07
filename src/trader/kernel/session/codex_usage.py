"""Codex 구독 관측. 공식 app-server 읽기 RPC만 사용하고 모델·턴은 시작하지 않는다.

https://developers.openai.com/codex/app-server
인증은 CLI에 맡기며 인증 파일, 계정 식별자, 원문 오류는 읽거나 반환하지 않는다.
"""

from __future__ import annotations

import json
import math
import os
import queue
import subprocess
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from trader.kernel.platform import codex_usage_process
from trader.kernel.platform.codex_bin import CodexNotFound, child_env, find_codex

CACHE_TTL_S = 60.0
RPC_TIMEOUT_S = 8.0
MAX_OUTPUT_BYTES = 256 * 1024
_LOCK = threading.Lock()
_CACHE: dict[tuple[str | None, str | None], tuple[float, dict]] = {}


class UsageReadError(Exception):
    """출력 가능한 분류 코드만 보존한다."""


def _rpc(bin_path: Path) -> dict:
    """stdin을 응답까지 열어 두는 stdio 핸드셰이크. stdout·시간·자식 수명을 제한한다."""
    reader = None
    try:
        with tempfile.TemporaryDirectory(prefix="codex-usage-") as cwd:
            with codex_usage_process.spawn(
                [str(bin_path), "app-server", "--stdio"],
                cwd=cwd,
                env=child_env(bin_path),
            ) as proc:
                inbox: queue.Queue = queue.Queue(maxsize=32)
                overflow = threading.Event()

                def read():
                    total = 0
                    assert proc.stdout is not None
                    pending = b""
                    while True:
                        try:
                            chunk = proc.stdout.read(65536)
                        except (OSError, ValueError):
                            break
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_OUTPUT_BYTES:
                            overflow.set()
                            return
                        pending += chunk
                        while b"\n" in pending:
                            line, pending = pending.split(b"\n", 1)
                            try:
                                inbox.put_nowait(json.loads(line.decode("utf-8", errors="replace")))
                            except (ValueError, UnicodeError):
                                continue
                            except queue.Full:
                                overflow.set()
                                return
                    try:
                        inbox.put_nowait(None)
                    except queue.Full:
                        overflow.set()

                reader = threading.Thread(target=read, daemon=True)
                reader.start()
                deadline = time.monotonic() + RPC_TIMEOUT_S

                def send(message):
                    assert proc.stdin is not None
                    proc.stdin.write(json.dumps(message).encode() + b"\n")
                    proc.stdin.flush()

                def receive(request_id):
                    while (remaining := deadline - time.monotonic()) > 0:
                        if overflow.is_set():
                            raise UsageReadError("invalid_response")
                        try:
                            message = inbox.get(timeout=min(remaining, 0.1))
                        except queue.Empty:
                            continue
                        if message is None:
                            raise UsageReadError("cli_error")
                        if not isinstance(message, dict) or message.get("id") != request_id:
                            continue
                        if "error" in message:
                            error = message["error"]
                            reason = (
                                str(error.get("message", "")).lower()
                                if isinstance(error, dict)
                                else ""
                            )
                            auth = any(
                                word in reason
                                for word in ("auth", "login", "logged", "sign in", "api key")
                            )
                            raise UsageReadError("authentication_required" if auth else "rpc_error")
                        if not isinstance(message.get("result"), dict):
                            raise UsageReadError("invalid_response")
                        return message["result"]
                    raise UsageReadError("timeout")

                send(
                    {
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "clientInfo": {
                                "name": "kis_usage_read",
                                "title": "Usage read",
                                "version": "0.1",
                            },
                            "capabilities": {
                                "experimentalApi": False,
                                "requestAttestation": False,
                                "explicitGatewayOauth": True,
                            },
                        },
                    }
                )
                receive(1)
                send({"method": "initialized"})
                send(
                    {
                        "id": 2,
                        "method": "account/rateLimits/read",
                        "params": {"excludeResetCreditDetails": True},
                    }
                )
                return receive(2)
    finally:
        if reader is not None:
            reader.join(timeout=1)


def _windows(result: dict) -> dict:
    buckets = result.get("rateLimitsByLimitId")
    snapshot = buckets.get("codex") if isinstance(buckets, dict) else None
    if snapshot is None:
        snapshot = result.get("rateLimits")
    if not isinstance(snapshot, dict):
        raise UsageReadError("invalid_response")
    windows = {"five_hour": None, "seven_day": None}
    for raw in (snapshot.get("primary"), snapshot.get("secondary")):
        if not isinstance(raw, dict):
            continue
        duration = raw.get("windowDurationMins")
        if not isinstance(duration, (int, float)):
            continue
        key = {300: "five_hour", 10080: "seven_day"}.get(duration)
        pct = raw.get("usedPercent")
        if key is None or isinstance(pct, bool) or not isinstance(pct, (int, float)):
            continue
        if not math.isfinite(pct) or not 0 <= pct <= 100:
            continue
        resets = raw.get("resetsAt")
        try:
            resets_at = (
                datetime.fromtimestamp(resets, UTC).isoformat()
                if not isinstance(resets, bool) and isinstance(resets, (int, float))
                else None
            )
        except (ValueError, OverflowError, OSError):
            resets_at = None
        windows[key] = {"used_pct": pct, "resets_at": resets_at}
    return windows


def read_limits(bin_override: str | None, *, now: datetime) -> dict:
    """동시 요청을 하나로 합치고 성공·실패 모두 60초 보관한다. 실패 시 이전값은 stale이다."""
    key = (bin_override, os.environ.get("CODEX_HOME"))
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None and time.monotonic() - cached[0] < CACHE_TTL_S:
            data = cached[1]
        else:
            previous = cached[1] if cached else {}
            try:
                windows = _windows(_rpc(find_codex(bin_override)))
                observed = any(windows.values())
                data = {
                    **windows,
                    "status": "ready" if observed else "unavailable",
                    "reason": None if observed else "not_provided",
                    "observed_at": now.isoformat(),
                }
            except (CodexNotFound, UsageReadError, OSError, subprocess.SubprocessError) as exc:
                reason = (
                    "cli_missing"
                    if isinstance(exc, CodexNotFound)
                    else (str(exc) if isinstance(exc, UsageReadError) else "cli_error")
                )
                stale = any(previous.get(k) for k in ("five_hour", "seven_day"))
                data = {
                    "five_hour": previous.get("five_hour"),
                    "seven_day": previous.get("seven_day"),
                    "status": "stale" if stale else "unavailable",
                    "reason": reason,
                    "observed_at": previous.get("observed_at"),
                }
            _CACHE[key] = (time.monotonic(), data)
        observed_at = data.get("observed_at")
        age = (
            max(0, (now - datetime.fromisoformat(observed_at)).total_seconds())
            if observed_at
            else None
        )
        return {**data, "age_s": age}
