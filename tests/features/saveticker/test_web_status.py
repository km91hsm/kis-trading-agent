"""수집 상태는 저장된 메타데이터만 읽고 원문·내부 경로를 노출하지 않는다."""

import json
from datetime import UTC, datetime

import pytest

from tests.features.conftest import web_client
from trader.features.saveticker.feature import FEATURE
from trader.features.saveticker.models import Capture
from trader.features.saveticker.paths import home
from trader.features.saveticker.store import Store, atomic_json

NOW = datetime(2026, 11, 4, 1, 0, tzinfo=UTC)
FIRST = "2026-11-04T00:59:00+00:00"
SECOND = "2026-11-04T00:59:30+00:00"


@pytest.fixture
def client(project):
    return web_client(project, [FEATURE], now=NOW)


def connect_bridge(monkeypatch, *, state="collecting", reason=None):
    """등록된 확장 + 살아 있는 Native host(합성 pid 999 가 잠금을 쥐고 최근에 기록)."""
    runtime = home() / "runtime"
    atomic_json(runtime / "bridge-config.json", {"backend": "extension"})
    row = {"backend": "extension", "state": state, "pid": 999, "at": SECOND, "updated_at": SECOND}
    if reason:
        row["reason"] = reason
    atomic_json(runtime / "bridge.json", row)
    atomic_json(runtime / "bridge-host.lock", {"pid": 999, "proc_start": "synthetic-start"})
    monkeypatch.setattr("trader.features.saveticker.web_status.pid_alive", lambda pid: pid == 999)
    monkeypatch.setattr(
        "trader.features.saveticker.web_status.same_process",
        lambda pid, start: pid == 999 and start == "synthetic-start",
    )


@pytest.fixture(autouse=True)
def live_bridge(project, monkeypatch):
    """기본은 확장이 연결돼 수집 중 — 저장 기록만으로 상태를 판정하는지 본다."""
    connect_bridge(monkeypatch)


def save(*, when=FIRST, stream="news_all", status="ok", items=None, reason=None):
    Store(home() / "data").save(
        Capture(
            stream=stream,
            page_url="https://www.saveticker.com/news",
            observed_at=when,
            status=status,
            reason=reason,
            items=items if items is not None else [{"id": "sample", "title": "합성 기사"}],
        )
    )


def get(client):
    response = client.get("/api/system/saveticker")
    assert response.status_code == 200
    return response.json()


def test_missing_collection_is_waiting_without_invented_expected_streams(project, client):
    before = sorted(p.relative_to(home()) for p in home().rglob("*"))
    data = get(client)
    assert data["streams"] == []
    assert data["summary"]["recorded"] == 0
    assert data["summary"]["state"] == "waiting"
    assert "expected" not in data["summary"]
    assert before == sorted(p.relative_to(home()) for p in home().rglob("*"))


def test_success_response_and_value_change_are_separate(project, client):
    save()
    save(when=SECOND)
    data = get(client)
    stream = data["streams"][0]
    assert stream["last_attempt_at"] == SECOND
    assert stream["last_response_at"] == SECOND
    assert stream["last_success_at"] == SECOND
    assert stream["last_change_at"] == FIRST
    assert stream["change_state"] == "unchanged"
    assert stream["state"] == "normal"
    assert data["summary"]["state"] == "normal"
    assert data["summary"]["recorded"] == 1


def test_zero_item_response_does_not_replace_last_useful_success(project, client):
    save()
    save(when=SECOND, status="partial", items=[], reason="empty_or_unavailable")
    stream = get(client)["streams"][0]
    assert stream["state"] == "empty"
    assert stream["count"] == 0
    assert stream["last_response_at"] == SECOND
    assert stream["last_success_at"] == FIRST
    assert stream["last_change_at"] == FIRST
    assert stream["change_state"] == "empty"


def test_latest_failure_keeps_last_response_and_sanitizes_reason(project, client):
    save()
    save(when=SECOND, status="auth_required", items=[], reason="/private/secret?token=bad")
    data = get(client)
    stream = data["streams"][0]
    assert stream["state"] == "login_required"
    assert stream["last_response_at"] == FIRST
    assert stream["last_success_at"] == FIRST
    assert stream["error"] == "로그인 또는 사이트 확인이 필요합니다"
    assert "secret" not in json.dumps(data)
    assert "합성 기사" not in json.dumps(data, ensure_ascii=False)


@pytest.mark.parametrize("stamp", ["2099-01-01T00:00:00+00:00", "broken", "2026-11-04T01:00:00"])
def test_invalid_timestamps_are_not_shown_as_recent_success(project, client, stamp):
    atomic_json(
        home() / "data/status.json",
        {
            "streams": {
                "news_all": {
                    "status": "ok",
                    "last_attempt_at": stamp,
                    "last_success_at": stamp,
                    "count": 1,
                    "target_interval_s": 30,
                }
            }
        },
    )
    data = get(client)
    stream = data["streams"][0]
    assert stream["state"] == "unknown"
    assert stream["last_attempt_at"] is None
    assert stream["last_success_at"] is None
    assert data["last_success_at"] is None
    assert data["summary"]["state"] != "normal"


@pytest.mark.parametrize("content", ['{"streams": []}', "{broken", '"' + "x" * 1_100_000 + '"'])
def test_corrupt_or_oversized_status_is_bounded_and_reported(project, client, content):
    root = home() / "data"
    root.mkdir(parents=True, exist_ok=True)
    (root / "status.json").write_text(content, encoding="utf-8")
    data = get(client)
    assert data["streams"] == []
    assert data["summary"]["state"] == "unknown"
    assert data["summary"]["note"]


def test_broken_history_keeps_status_and_does_not_claim_unchanged(project, client):
    save()
    (home() / "data/feed.sqlite3").write_bytes(b"broken database")
    data = get(client)
    stream = data["streams"][0]
    assert stream["last_success_at"] == FIRST
    assert stream["last_change_at"] is None
    assert stream["change_state"] == "unknown"
    assert data["summary"]["note"]


def test_stale_success_is_delayed_and_partial_is_not_normal(project, client):
    save(when="2026-11-04T00:00:00+00:00")
    save(stream="market_turnover_live", when=SECOND, status="partial", reason="scroll_limit")
    streams = {s["id"]: s for s in get(client)["streams"]}
    assert streams["news_all"]["state"] == "delayed"
    assert streams["market_turnover_live"]["state"] == "partial"


def test_lane_failure_is_visible_with_healthy_process_and_recent_old_values(project, client):
    save()
    save(stream="market_gain_live")
    atomic_json(
        home() / "runtime/runner-market.json",
        {
            "backend": "extension",
            "state": "error",
            "error_type": "TimeoutError",
            "updated_at": SECOND,
        },
    )
    data = get(client)
    streams = {s["id"]: s for s in data["streams"]}
    assert streams["news_all"]["state"] == "normal"
    assert streams["market_gain_live"]["state"] == "error"
    assert data["summary"]["state"] == "partial"


def test_newer_history_cannot_be_mixed_with_an_older_status_snapshot(project, client):
    save()
    previous = (home() / "data/status.json").read_text(encoding="utf-8")
    save(when=SECOND, items=[{"id": "sample", "title": "합성 기사 수정"}])
    (home() / "data/status.json").write_text(previous, encoding="utf-8")
    stream = get(client)["streams"][0]
    assert stream["last_attempt_at"] == FIRST
    assert stream["last_change_at"] == FIRST
    assert stream["change_state"] == "changed"


def test_unknown_stream_history_does_not_claim_no_error(project, client):
    atomic_json(home() / "data/status.json", {"streams": {"news_all": "corrupt"}})
    data = get(client)
    assert data["streams"][0]["state"] == "unknown"
    assert data["streams"][0]["error"]
    assert data["summary"]["state"] == "unknown"


def test_collection_query_does_not_modify_collection_records(project, client):
    save()
    before = {str(p.relative_to(home())): p.read_bytes() for p in home().rglob("*") if p.is_file()}
    get(client)
    # 읽기 연결의 WAL/SHM 잠금 파일은 SQLite 표준 동작이며 수집 기록이 아니다.
    after = {
        str(p.relative_to(home())): p.read_bytes()
        for p in home().rglob("*")
        if p.is_file() and not p.name.endswith(("-wal", "-shm"))
    }
    assert after == before


@pytest.mark.parametrize(
    "bridge_at, expected", [(SECOND, "healthy"), ("2099-01-01T00:00:00Z", "waiting_chrome")]
)
def test_extension_health_requires_current_heartbeat_and_live_holder(
    project, client, monkeypatch, bridge_at, expected
):
    save()
    atomic_json(home() / "runtime/bridge-config.json", {"backend": "extension"})
    atomic_json(
        home() / "runtime/bridge.json",
        {
            "state": "collecting",
            "pid": 999,
            "at": bridge_at,
            "updated_at": bridge_at,
        },
    )
    atomic_json(home() / "runtime/bridge-host.lock", {"pid": 999, "proc_start": "synthetic-start"})
    monkeypatch.setattr("trader.features.saveticker.web_status.pid_alive", lambda pid: pid == 999)
    monkeypatch.setattr(
        "trader.features.saveticker.web_status.same_process",
        lambda pid, start: pid == 999 and start == "synthetic-start",
    )
    data = get(client)
    assert data["configured"] is True
    assert data["health"] == expected
    assert data["service"]["state"] == ("collecting" if expected == "healthy" else "waiting_chrome")


def test_history_connection_rejects_writes(project, client, monkeypatch):
    import sqlite3

    save()
    connect = sqlite3.connect

    def readonly_connection(*args, **kwargs):
        connection = connect(*args, **kwargs)
        if kwargs.get("uri"):
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                connection.execute("DELETE FROM captures")
        return connection

    monkeypatch.setattr(
        "trader.features.saveticker.web_status.sqlite3.connect", readonly_connection
    )
    data = get(client)
    assert data["streams"][0]["last_change_at"] == FIRST
    with connect(home() / "data/feed.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM captures").fetchone()[0] == 1


def test_old_calendar_and_unconfirmed_targets_do_not_create_current_outage(project, client):
    save(when=SECOND)
    save(stream="calendar_2026-08", when="2026-08-01T00:00:00+00:00")
    save(stream="calendar_2026-12", when="2026-10-01T00:00:00+00:00")
    save(stream="market_turnover_regular", when="2026-10-01T00:00:00+00:00")
    data = get(client)
    streams = {s["id"]: s for s in data["streams"]}
    assert streams["news_all"]["scope"] == "active"
    assert streams["calendar_2026-08"]["scope"] == "historical"
    assert streams["calendar_2026-12"]["scope"] == "unconfirmed"
    assert streams["market_turnover_regular"]["scope"] == "unconfirmed"
    assert data["summary"]["state"] == "normal"
    assert data["summary"]["recorded"] == 4
    assert data["summary"]["active_recorded"] == 1
    assert data["summary"]["delayed"] == 0
    assert data["summary"]["historical"] == 1
    assert data["summary"]["unconfirmed"] == 2


def test_only_historical_collection_is_waiting_without_active_target_evidence(project, client):
    save(stream="calendar_2026-08", when="2026-08-01T00:00:00+00:00")
    data = get(client)
    assert data["summary"]["state"] == "waiting"
    assert data["summary"]["active_recorded"] == 0


@pytest.mark.parametrize(
    "bridge, expected",
    [
        ({"state": "auth_required", "reason": "login_required"}, "login_required"),
        ({"state": "waiting_verification", "reason": "verification_required"}, "login_required"),
        ({"state": "paused"}, "stopped"),
        ({"state": "connecting"}, "waiting"),
    ],
)
def test_summary_does_not_hide_stopped_service_behind_recent_data(
    project, client, monkeypatch, bridge, expected
):
    save(when=SECOND)
    connect_bridge(monkeypatch, **bridge)
    assert get(client)["summary"]["state"] == expected


def test_disconnected_extension_is_waiting_and_pauses_lanes(project, client):
    save(when=SECOND)
    (home() / "runtime/bridge.json").unlink()
    data = get(client)
    assert data["configured"] is True
    assert data["service"]["state"] == "waiting_chrome" and data["health"] == "waiting_chrome"
    assert data["summary"]["state"] == "waiting"
    assert data["recovery"]["state"] == "waiting" and "Chrome" in data["recovery"]["text"]
    assert {lane["state"] for lane in data["lanes"].values()} == {"paused"}


def test_blocked_extension_reports_manual_recovery_without_legacy_fields(
    project, client, monkeypatch
):
    connect_bridge(monkeypatch, state="waiting_verification", reason="verification_required")
    data = get(client)
    assert data["blocked"] is True and data["health"] == "blocked"
    assert data["recovery"]["state"] == "manual" and "재확인" in data["recovery"]["text"]
    assert "backend" not in data and "lane_blocks" not in data


def test_unregistered_extension_is_not_configured(project, client):
    (home() / "runtime/bridge-config.json").unlink()
    assert get(client)["configured"] is False


def test_current_lane_error_is_visible_even_without_a_saved_stream(project, client):
    save(when=SECOND)
    atomic_json(
        home() / "runtime/runner-background.json",
        {
            "backend": "extension",
            "state": "error",
            "error_type": "TimeoutError",
            "updated_at": SECOND,
        },
    )
    assert get(client)["summary"]["state"] == "partial"


def test_blocked_extension_with_retrying_recovery_is_not_shown_as_stopped(
    project, client, monkeypatch
):
    connect_bridge(monkeypatch, state="waiting_verification", reason="verification_required")
    path = home() / "runtime/extension-diagnostics.json"
    row = {"category": "recheck", "reason": "page_unverified", "reload_count": 2,
           "recorded_at": FIRST}
    atomic_json(path, {**row, "terminal": False})
    data = get(client)
    assert data["recovery"]["state"] == "retrying"
    assert data["recovery"]["text"].startswith("복구 재시도 대기")
    atomic_json(path, row)
    assert get(client)["recovery"]["state"] == "manual"
