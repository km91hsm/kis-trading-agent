from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from trader.features.saveticker import automation


def snapshot(now):
    runners = {
        name: {"pid": index + 1, "state": "collecting", "exit_code": None}
        for index, name in enumerate(automation.RUNNERS)
    }
    status = {
        "streams": {
            name: {"count": 1, "status": "ok", "last_success_at": now.isoformat()}
            for name in automation.STREAMS
        }
    }
    return runners, status


def test_healthy_monitor_and_trends_are_ignored():
    now = datetime.now(UTC)
    r, s = snapshot(now)
    s["streams"]["community_trends"] = {"status": "error"}
    state = automation.evaluate(r, s, {}, now=now, alive=lambda pid: True)
    assert state["health"] == "healthy" and not state["faults"]


def test_dead_process_immediate_and_stale_stream_two_checks():
    now = datetime.now(UTC)
    r, s = snapshot(now)
    s["streams"]["news_all"]["last_success_at"] = (now - timedelta(seconds=150)).isoformat()
    first = automation.evaluate(r, s, {}, now=now, alive=lambda pid: True)
    assert not first["faults"]
    second = automation.evaluate(r, s, first, now=now, alive=lambda pid: True)
    assert second["faults"] == ["stale:news_all"]
    dead = automation.evaluate(r, s, {}, now=now, alive=lambda pid: False)
    assert any(x.startswith("process:") for x in dead["faults"])


def test_startup_grace_never_hides_process_death():
    now = datetime.now(UTC)
    r, s = snapshot(now)
    s["streams"]["news_all"]["last_success_at"] = None
    first = automation.evaluate(r, s, {}, now=now, alive=lambda pid: True)
    second = automation.evaluate(
        r, s, first, now=now + timedelta(seconds=60), alive=lambda pid: True
    )
    assert not second["faults"]
    dead = automation.evaluate(r, s, first, now=now, alive=lambda pid: False)
    assert dead["faults"]


def test_file_errors_require_two_checks():
    now = datetime.now(UTC)
    r, s = snapshot(now)
    first = automation.evaluate(r, None, {}, now=now, alive=lambda pid: True)
    assert not first["faults"]
    second = automation.evaluate(r, None, first, now=now, alive=lambda pid: True)
    assert "file:status" in second["faults"]


def test_monitor_notifies_fault_once_and_recovery_once(tmp_path, monkeypatch):
    from trader.features.saveticker.store import atomic_json

    now = datetime.now(UTC)
    runners, status = snapshot(now)
    runtime = tmp_path / "runtime"
    for name, row in runners.items():
        atomic_json(runtime / (name + ".json"), row)
    atomic_json(tmp_path / "data/status.json", status)
    sent = []
    send = lambda message: sent.append(message) or True  # noqa: E731
    evaluate = automation.evaluate
    monkeypatch.setattr(
        automation, "evaluate", lambda *args, **kw: evaluate(*args, **kw, alive=lambda pid: False)
    )
    automation.monitor(tmp_path, send=send)
    automation.monitor(tmp_path, send=send)
    assert len(sent) == 1
    monkeypatch.setattr(
        automation, "evaluate", lambda *args, **kw: evaluate(*args, **kw, alive=lambda pid: True)
    )
    automation.monitor(tmp_path, send=send)
    automation.monitor(tmp_path, send=send)
    assert len(sent) == 2 and "복구" in sent[-1]


def test_status_uses_capture_timestamps_without_monitor(tmp_path):
    from trader.features.saveticker.store import atomic_json

    atomic_json(tmp_path / "data/status.json", {"streams": {
        "news_all": {"last_success_at": "2026-11-06T01:00:00+00:00"},
        "market_gain_live": {"last_success_at": "2026-11-06T02:00:00+00:00"},
    }})
    assert automation.status(tmp_path)["last_success_at"] == "2026-11-06T02:00:00+00:00"


def test_live_extension_requiring_verification_is_not_healthy(tmp_path):
    import os

    from trader.features.saveticker.store import atomic_json
    from trader.kernel.daemon.lock import Lock

    runtime = tmp_path / "runtime"
    atomic_json(runtime / "bridge-config.json", {"backend": "extension"})
    atomic_json(runtime / "bridge.json", {"pid": os.getpid(),
        "updated_at": datetime.now(UTC).isoformat(), "state": "waiting_verification",
        "reason": "verification_required"})
    lock = Lock(runtime / "bridge-host.lock")
    assert lock.acquire()
    try:
        result = automation.status(tmp_path)
        assert result["blocked"] is True
        assert result["health"] != "healthy"
        assert "재확인" in result["reason_text"]
    finally:
        lock.release()


def test_disconnected_extension_waits_for_chrome_and_pauses_lanes(tmp_path):
    from trader.features.saveticker.store import atomic_json

    runtime = tmp_path / "runtime"
    assert automation.registered(tmp_path) is False
    atomic_json(runtime / "bridge-config.json", {"backend": "extension"})
    atomic_json(runtime / "runner-news.json", {"backend": "extension", "state": "collecting"})
    atomic_json(runtime / "runner-market.json", {"state": "error", "error_type": "AccessDenied"})
    result = automation.status(tmp_path)
    assert result["registered"] is True
    assert result["service"]["state"] == "waiting_chrome" and result["health"] == "waiting_chrome"
    assert result["blocked"] is False
    assert result["lanes"]["news"]["state"] == "paused"
    # 확장이 쓰지 않은 작업 기록(옛 수집기 잔재)은 보이지 않는다
    assert result["lanes"]["market"]["error_type"] is None


def test_collector_alive_matches_native_host_only(monkeypatch):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=out)

    monkeypatch.setattr(automation.os, "name", "posix")
    monkeypatch.setattr(automation.subprocess, "run", run)
    out = "/usr/bin/python -m trader saveticker native-host chrome-extension://x/"
    assert automation.collector_alive(123) is True
    out = "/usr/bin/python -m trader saveticker run --headed"
    assert automation.collector_alive(123) is False
    assert automation.collector_alive(0) is False and len(calls) == 2


def test_status_marks_non_terminal_recheck_diagnostic_as_retry_wait(tmp_path):
    from trader.features.saveticker.store import atomic_json

    runtime = tmp_path / "runtime"
    assert automation.status(tmp_path)["diagnostic"] is None
    row = {"category": "recheck", "reason": "page_unverified", "reload_count": 2,
           "recorded_at": "2026-11-08T00:00:00+00:00"}
    atomic_json(runtime / "extension-diagnostics.json", {**row, "terminal": False})
    waiting = automation.status(tmp_path)["diagnostic"]
    assert waiting["terminal"] is False and waiting["text"].startswith("복구 재시도 대기")
    # 옛 기록(키 없음)은 중지로 본다
    atomic_json(runtime / "extension-diagnostics.json", row)
    stopped = automation.status(tmp_path)["diagnostic"]
    assert stopped["terminal"] is True and "복구 재시도 대기" not in stopped["text"]
    assert stopped["reason"] == "page_unverified"


def test_status_old_wait_diagnostic_before_new_block_is_not_retrying(tmp_path):
    """차단 중일 때 진단이 지금 차단 시각(bridge `at`)보다 오래되면
    「복구 재시도 대기」가 아니다."""
    import os

    from trader.features.saveticker.store import atomic_json
    from trader.kernel.daemon.lock import Lock

    runtime = tmp_path / "runtime"
    now = datetime.now(UTC)
    blocked_at = now.isoformat()
    atomic_json(runtime / "bridge.json", {"pid": os.getpid(), "state": "waiting_verification",
                                          "reason": "verification_required",
                                          "updated_at": blocked_at, "at": blocked_at})
    row = {"category": "recheck", "reason": "page_unverified", "reload_count": 2,
           "terminal": False}
    lock = Lock(runtime / "bridge-host.lock")
    assert lock.acquire()
    try:
        atomic_json(runtime / "extension-diagnostics.json",
                    {**row, "recorded_at": (now - timedelta(minutes=5)).isoformat()})
        stale = automation.status(tmp_path)
        atomic_json(runtime / "extension-diagnostics.json", {**row, "recorded_at": blocked_at})
        fresh = automation.status(tmp_path)
    finally:
        lock.release()
    assert stale["blocked"] is True
    assert stale["diagnostic"]["retrying"] is False
    assert "복구 재시도 대기" not in stale["diagnostic"]["text"]
    assert fresh["diagnostic"]["retrying"] is True
    assert fresh["diagnostic"]["text"].startswith("복구 재시도 대기")
