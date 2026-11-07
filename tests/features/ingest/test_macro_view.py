"""저장된 합성 체크포인트만 읽는 매크로 API 계약."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

import pytest

from tests.features.conftest import web_client
from trader.features.ingest.feature import FEATURE

NOW = datetime(2026, 11, 7, 6, tzinfo=UTC)


def _section(value=6000, day="2026-11-06", **extra):
    return {
        "data": {"value": value, "date": day},
        "received_at": NOW.isoformat(),
        "error": None,
        **extra,
    }


def _save(project, macro, *, ticker="TEST", market="KR", saved=NOW, meta=None):
    path = (
        project / "state" / "runs" / saved.date().isoformat() / market / ticker / "00_inputs.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {
            "market": market,
            "asof": saved.isoformat(),
            "generated_at": saved.isoformat(),
            **(meta or {}),
        },
        "news_macro": {"macro": macro},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    os.utime(path, (saved.timestamp(), saved.timestamp()))
    return path


def _response(client, market="KR"):
    response = client.get("/api/macro", params={"market": market})
    assert response.status_code == 200
    return response.json()


def _items(client, market="KR"):
    return {row["key"]: row for row in _response(client, market)["items"]}


def test_empty_macro_is_read_only_and_market_is_validated(project):
    client = web_client(project, [FEATURE], now=NOW)
    before = set((project / "state").rglob("*"))
    rows = _items(client)
    assert list(rows) == ["sp500", "nasdaq", "us10y", "wti", "dxy"]
    assert all(row["value"] is None and row["status"] == "unavailable" for row in rows.values())
    assert all(row["reason"] == "저장된 유효 관측값 없음" for row in rows.values())
    assert set((project / "state").rglob("*")) == before
    assert client.get("/api/macro?market=JP").status_code == 400


def test_legacy_daily_values_and_proxy_identity_are_preserved_without_fake_changes(project):
    client = web_client(project, [FEATURE], now=NOW)
    path = _save(
        project,
        {
            "sp500": _section("6000.50"),
            "nasdaq": _section(20000),
            "us10y": _section(4.25),
            "wti": _section(72.5),
            "dxy": _section(123.4),
        },
    )
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    rows = _items(client)
    assert rows["sp500"]["value"] == "6000.50"
    assert rows["sp500"]["source"] == "FRED"
    assert rows["sp500"]["source_metadata"] == "legacy_mapping"
    assert rows["nasdaq"]["instrument"] == "NASDAQ Composite"
    assert rows["us10y"]["unit"] == "%"
    assert rows["wti"]["proxy"] is True and "현물" in rows["wti"]["proxy_reason"]
    assert rows["dxy"]["proxy"] is True and "광의" in rows["dxy"]["proxy_reason"]
    assert rows["dxy"]["series_id"] == "DTWEXBGS"
    assert rows["sp500"]["observed_date"] == "2026-11-06"
    assert rows["sp500"]["stored_at"] == NOW.isoformat()
    assert rows["sp500"]["freshness"]["status"] == "unknown"
    assert rows["sp500"]["live_verified"] is False
    assert all(
        row["change_pct"] is None and "미제공" in row["change_reason"] for row in rows.values()
    )
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_latest_valid_observation_wins_over_latest_save_and_other_market(project):
    client = web_client(project, [FEATURE], now=NOW)
    _save(project, {"sp500": _section(6000)}, ticker="A", saved=NOW - timedelta(minutes=2))
    _save(project, {"sp500": _section(5900, "2026-11-05")}, ticker="B")
    _save(project, {"sp500": _section(9000)}, ticker="US", market="US")
    assert _items(client)["sp500"]["value"] == "6000"
    assert _items(client, "US")["sp500"]["value"] == "9000"


def test_same_day_and_mixed_sources_do_not_invent_change(project):
    client = web_client(project, [FEATURE], now=NOW)
    _save(project, {"sp500": _section(5000)}, ticker="A", saved=NOW - timedelta(days=1))
    _save(project, {"sp500": _section(6000, source="KIS", series_id="SPX")}, ticker="B")
    _save(project, {"sp500": _section(7000, source="KIS", series_id="SPX")}, ticker="C")
    row = _items(client)["sp500"]
    assert row["value"] in {"6000", "7000"}
    assert row["change_pct"] is None
    assert row["change_reason"] == "원천 등락률 미제공 · 저장값 간 임의 계산 안 함"


def test_explicit_change_is_preserved_as_ratio_including_zero(project):
    client = web_client(project, [FEATURE], now=NOW)
    s = _section(source="KIS", series_id="SPX")
    s["data"]["change_pct"] = 0
    n = _section(source="KIS", series_id="COMP")
    n["data"]["change_pct"] = -0.0125
    _save(project, {"sp500": s, "nasdaq": n})
    rows = _items(client)
    assert rows["sp500"]["change_pct"] == 0
    assert rows["nasdaq"]["change_pct"] == -0.0125
    assert rows["sp500"]["change_reason"] is None


@pytest.mark.parametrize(
    "bad",
    [
        {"data": {"value": 100, "date": "2026-11-08"}},
        {"data": {"value": 100, "asof": "2026-11-07T02:00:00-05:00"}},
        {"received_at": "2026-11-07T06:00:01Z"},
        {"data": {"value": "NaN", "date": "2026-11-06"}},
        {"data": {"value": True, "date": "2026-11-06"}},
        {"data": {"value": 100, "date": "bad-date"}},
        {"data": {"value": 100, "asof": "2026-11-07T05:00:00"}},
        {"data": {"value": 100, "date": "2026-11-06"}, "error": "failed"},
        {"source": "KIS", "series_id": "NDX", "instrument": "NASDAQ 100"},
    ],
)
def test_invalid_newer_values_cannot_hide_valid_observation(project, bad):
    client = web_client(project, [FEATURE], now=NOW)
    _save(project, {"sp500": _section(5000)}, ticker="OLD", saved=NOW - timedelta(minutes=2))
    _save(project, {"sp500": {**_section(9000), **bad}}, ticker="BAD")
    row = _items(client)["sp500"]
    assert row["value"] == "5000"
    assert row["stored_at"] == (NOW - timedelta(minutes=2)).isoformat()


def test_corrupt_wrong_shape_future_metadata_and_symlinks_are_ignored(project):
    client = web_client(project, [FEATURE], now=NOW)
    corrupt = _save(project, {}, ticker="CORRUPT")
    corrupt.write_text("{broken", encoding="utf-8")
    wrong = _save(project, {}, ticker="WRONG")
    wrong.write_text("[]", encoding="utf-8")
    _save(
        project,
        {"sp500": _section(8000)},
        ticker="FUTURE",
        meta={"generated_at": (NOW + timedelta(minutes=1)).isoformat()},
    )
    _save(project, {"sp500": _section(7000)}, ticker="MARKET", meta={"market": "US"})
    external = project / "not-a-snapshot.json"
    external.write_text(json.dumps({"news_macro": {"macro": {"sp500": _section(9999)}}}))
    link = corrupt.parent.parent / "LINK"
    link.mkdir()
    (link / "00_inputs.json").symlink_to(external)
    rows = _items(client)
    assert rows["sp500"]["value"] is None
    assert _response(client)["scan"]["skipped"] >= 4


def test_old_observation_is_not_claimed_fresh_after_recent_save(project):
    client = web_client(project, [FEATURE], now=NOW)
    _save(project, {"sp500": _section(6000, "2026-10-20")})
    row = _items(client)["sp500"]
    assert row["value"] == "6000"
    assert row["freshness"]["observation_age_days"] == 18
    assert row["freshness"]["status"] == "unknown"
    assert "발표 시각" in row["freshness"]["reason"]


def test_macro_never_collects_or_uses_network_and_scan_is_bounded(project, monkeypatch):
    from trader.features.ingest import macro

    client = web_client(project, [FEATURE], now=NOW)

    def forbidden(*args, **kwargs):
        raise AssertionError("조회가 수집기를 호출하면 안 됩니다")

    monkeypatch.setattr(macro, "collect_macro", forbidden)
    _save(project, {"sp500": _section(5000)}, saved=NOW - timedelta(days=40))
    row = _items(client)["sp500"]
    assert row["value"] is None
    scan = _response(client)["scan"]
    assert scan["lookback_days"] == 31 and scan["max_files"] == 200
    assert scan["files_read"] == 0


@pytest.mark.parametrize(
    "metadata",
    [
        {"instrument": "NASDAQ 100"},
        {"source_asof": "2026-11-07T07:00:00Z"},
        {"observed_date": "2026-11-08"},
        {"data": {"value": 9000, "date": "2026-11-06", "symbol": "NDX"}},
    ],
)
def test_conflicting_instrument_or_future_source_metadata_is_not_relabelled(project, metadata):
    client = web_client(project, [FEATURE], now=NOW)
    _save(project, {"sp500": _section(5000)}, ticker="OLD", saved=NOW - timedelta(minutes=2))
    _save(
        project,
        {"sp500": {**_section(9000, source="KIS", series_id="SPX"), **metadata}},
        ticker="BAD",
    )
    assert _items(client)["sp500"]["value"] == "5000"


def test_file_and_byte_limits_are_enforced_without_reading_unbounded_history(project):
    from trader.features.ingest.macro_view import overview

    for n in range(201):
        _save(project, {"sp500": _section(5000)}, ticker=f"T{n:03}")
    scan = overview(project / "state", "KR", now=NOW)["scan"]
    assert scan["files_checked"] == 200
    assert scan["files_read"] == 200
    assert scan["truncated"] is True


def test_oversized_snapshot_is_skipped_before_reading_and_valid_history_remains(project):
    from trader.features.ingest.macro_view import overview

    _save(project, {"sp500": _section(5000)}, ticker="OLD", saved=NOW - timedelta(minutes=2))
    huge = _save(project, {"sp500": _section(9000)}, ticker="HUGE")
    with huge.open("wb") as file:
        file.truncate(4 * 1024 * 1024 + 1)
    os.utime(huge, (NOW.timestamp(), NOW.timestamp()))
    result = overview(project / "state", "KR", now=NOW)
    assert result["items"][0]["value"] == "5000"
    assert result["scan"]["bytes_read"] < 1024
    assert result["scan"]["skipped"] == 1
