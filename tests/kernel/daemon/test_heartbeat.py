"""데몬 하트비트: 파일 왕복, 쓰기 실패 무시, 재시작 이어받기, read_status 의 alive/stale 판정."""

import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from trader.kernel.daemon import heartbeat
from trader.kernel.daemon.heartbeat import Heartbeat, read_status

NOW = datetime(2026, 10, 23, 3, 0, tzinfo=UTC)


class Clock:
    def __init__(self, at: datetime) -> None:
        self.at = at

    def __call__(self) -> datetime:
        return self.at


def _job(id_, kind, market=None, cron=None, interval_s=None):
    return SimpleNamespace(id=id_, kind=kind, market=market, cron=cron, interval_s=interval_s)


JOBS = [
    _job("kr_scan", "scan", "KR", cron={"hour": 7, "minute": 30}),
    _job("kr_monitor", "monitor", "KR", interval_s=60),
]


def _read(tmp_path):
    return json.loads((tmp_path / heartbeat.FILE_NAME).read_text(encoding="utf-8"))


def test_heartbeat_round_trip(tmp_path):
    clock = Clock(NOW)
    hb = Heartbeat(tmp_path, clock)
    hb.start(pid=4242, jobs=JOBS)
    clock.at = NOW + timedelta(seconds=5)
    hb.job_started("kr_monitor")
    clock.at = NOW + timedelta(seconds=6)
    hb.job_finished("kr_monitor", "ok")
    hb.job_started("kr_scan")
    hb.job_finished("kr_scan", "error", error="boom")
    clock.at = NOW + timedelta(minutes=4)
    hb.set_next("kr_monitor", NOW + timedelta(minutes=5))
    data = _read(tmp_path)
    assert data["pid"] == 4242 and data["started_at"] == NOW.isoformat()
    assert data["updated_at"] == (NOW + timedelta(minutes=4)).isoformat()
    j = data["jobs"]
    assert j["kr_monitor"]["last_status"] == "ok" and j["kr_monitor"]["kind"] == "monitor"
    assert j["kr_monitor"]["next_run"] == (NOW + timedelta(minutes=5)).isoformat()
    assert j["kr_scan"]["last_status"] == "error" and j["kr_scan"]["last_error"] == "boom"
    clock.at = NOW + timedelta(minutes=5)
    hb.stop()
    assert _read(tmp_path)["stopped_at"] == (NOW + timedelta(minutes=5)).isoformat()


def test_beat_updates_timestamp_and_optional_job(tmp_path):
    clock = Clock(NOW)
    hb = Heartbeat(tmp_path, clock)
    hb.start(pid=1, jobs=JOBS)
    clock.at = NOW + timedelta(seconds=30)
    hb.beat()
    assert _read(tmp_path)["updated_at"] == clock.at.isoformat()
    clock.at = NOW + timedelta(seconds=60)
    hb.beat("kr_monitor")
    data = _read(tmp_path)
    assert data["updated_at"] == clock.at.isoformat()
    assert data["jobs"]["kr_monitor"]["last_beat"] == clock.at.isoformat()


def test_restart_keeps_previous_last_state_but_not_running(tmp_path):
    hb = Heartbeat(tmp_path, Clock(NOW))
    hb.start(pid=1, jobs=JOBS)
    hb.job_started("kr_scan")
    hb.job_finished("kr_scan", "error", error="boom")
    hb.job_started("kr_monitor")  # 죽기 직전 '실행 중'
    Heartbeat(tmp_path, Clock(NOW + timedelta(hours=1))).start(pid=2, jobs=JOBS)
    j = _read(tmp_path)["jobs"]
    assert j["kr_scan"]["last_status"] == "error" and j["kr_scan"]["last_error"] == "boom"
    assert j["kr_monitor"]["last_status"] is None


def test_write_failure_is_swallowed(tmp_path):
    hb = Heartbeat(tmp_path / "no_such_dir" / "x", Clock(NOW))
    hb.start(pid=1, jobs=JOBS)  # 디렉터리가 없어도 예외 없음
    hb.job_started("kr_monitor")


def test_read_status_without_daemon_lists_schedule(tmp_path):
    st = read_status(tmp_path, NOW, JOBS)
    assert st["alive"] is False and st["pid"] is None and st["stale"] is False
    assert [j["id"] for j in st["jobs"]] == ["kr_scan", "kr_monitor"]
    assert st["jobs"][0]["cron"] == {"hour": 7, "minute": 30} and st["jobs"][1]["interval_s"] == 60
    assert st["jobs"][1]["last_status"] is None


def test_read_status_alive_and_stale(tmp_path):
    start = NOW - timedelta(hours=3)
    (tmp_path / heartbeat.LOCK_NAME).write_text(
        json.dumps({"pid": os.getpid(), "started_at": start.isoformat()}), encoding="utf-8"
    )
    hb = Heartbeat(tmp_path, Clock(start))
    hb.start(pid=os.getpid(), jobs=JOBS)
    st = read_status(tmp_path, NOW, JOBS)
    assert st["alive"] is True and st["pid"] == os.getpid()
    assert st["stale"] is True and st["age_s"] == 3 * 3600 and st["uptime_s"] == 3 * 3600
    assert st["pid_mismatch"] is False
    fresh = read_status(tmp_path, start + timedelta(seconds=30), JOBS)
    assert fresh["stale"] is False


def test_read_status_dead_lock_and_pid_mismatch(tmp_path):
    (tmp_path / heartbeat.LOCK_NAME).write_text(
        json.dumps({"pid": 2**31 - 5, "started_at": NOW.isoformat()}), encoding="utf-8"
    )
    assert read_status(tmp_path, NOW, JOBS)["alive"] is False
    (tmp_path / heartbeat.LOCK_NAME).write_text(
        json.dumps({"pid": os.getpid(), "started_at": NOW.isoformat()}), encoding="utf-8"
    )
    Heartbeat(tmp_path, Clock(NOW)).start(pid=99, jobs=JOBS)
    assert read_status(tmp_path, NOW, JOBS)["pid_mismatch"] is True


def test_read_status_corrupt_files(tmp_path):
    (tmp_path / heartbeat.LOCK_NAME).write_text("{not json", encoding="utf-8")
    (tmp_path / heartbeat.FILE_NAME).write_text("[1, 2]", encoding="utf-8")
    st = read_status(tmp_path, NOW, JOBS)
    assert st["alive"] is False and st["updated_at"] is None and st["age_s"] is None
    assert len(st["jobs"]) == 2
