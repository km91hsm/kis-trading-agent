"""운영 요약용 시장 시계는 저장소/브로커 호출 없이 서버 시간으로 판단한다."""
from dataclasses import replace
from datetime import UTC, datetime

from tests.kernel.web.conftest import ledger_deps, make_client


def test_market_clock_shows_regular_hours_and_calendar_uncertainty(project):
    deps = replace(ledger_deps(project), now=lambda: datetime(2026, 11, 6, 1, tzinfo=UTC))
    r = make_client(deps).get("/api/system/market-clock")
    assert r.status_code == 200
    got = r.json()
    assert got["markets"]["KR"]["schedule_open"] is True
    assert got["markets"]["US"]["schedule_open"] is False
    assert got["markets"]["KR"]["holiday_verified"] is False
    assert got["basis"] == "regular_calendar_rules"


def test_market_clock_weekend_and_us_dst_use_shared_calendar(project):
    deps = replace(ledger_deps(project), now=lambda: datetime(2026, 11, 7, 5, tzinfo=UTC))
    got = make_client(deps).get("/api/system/market-clock").json()
    assert all(not m["schedule_open"] for m in got["markets"].values())
    assert got["markets"]["US"]["timezone"] == "America/New_York"
    assert got["markets"]["KR"]["local_date"] == "2026-11-07"
