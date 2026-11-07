"""세이브티커 로컬 폴더 리더(features/saveticker/reader): 뉴스 읽기·시장 분리·발행시각·스냅샷 asof·
종목 언급."""

import shutil
from datetime import UTC, datetime
from pathlib import Path

from trader.features.saveticker import reader as saveticker

SAMPLE = Path(__file__).parent / "fixtures" / "saveticker"
T_AFTER = datetime(2026, 10, 25, 3, 0, tzinfo=UTC)  # 샘플 관측 뒤
T_BEFORE = datetime(2026, 10, 25, 1, 0, tzinfo=UTC)  # 샘플 관측 전


def _root(tmp_path: Path) -> Path:
    dst = tmp_path / "saveticker"
    shutil.copytree(SAMPLE, dst)
    return dst


def test_resolve_dir_and_available(project):
    assert saveticker.resolve_dir(None) is None
    p = saveticker.resolve_dir("state/external/saveticker")
    assert p == project / "state" / "external" / "saveticker" and not saveticker.available(p)
    assert saveticker.snapshot(p, asof=T_AFTER) is None
    assert saveticker.available(SAMPLE)


def test_published_at_and_markets():
    assert (
        saveticker.published_at_of({"published_at": "2026-10-24T23:30:00+00:00"})
        == "2026-10-24T23:30:00+00:00"
    )
    assert (
        saveticker.published_at_of({"displayed_time": "2026. 10. 24. 07:40"})
        == "2026-10-23T22:40:00+00:00"
    )
    assert saveticker.published_at_of({"displayed_time": "2시간 전"}) is None
    assert saveticker.markets_of({"tickers": []}) == ["KR", "US"]
    assert saveticker.markets_of({"tickers": ["005930"]}) == ["KR"]
    assert saveticker.markets_of({"tickers": ["GSK"]}) == ["US"]
    assert saveticker.markets_of({"tickers": ["005930", "AAPL"]}) == ["KR", "US"]


def test_read_news_keeps_last_revision_and_report_excerpt(tmp_path):
    root = _root(tmp_path)
    items = saveticker.read_news_items(root, today=T_AFTER.date())
    ids = {i["id"] for i in items}
    assert len(items) == len(ids) and any(i["_stream"] == "news_reports" for i in items)
    report = next(i for i in items if i["_stream"] == "news_reports")
    assert report["report_date"] == "2026-10-23"
    excerpt = saveticker.report_excerpt(root, report)
    assert excerpt and "S&P500" in excerpt and len(excerpt) <= saveticker.REPORT_EXCERPT_CHARS
    assert len(saveticker.report_excerpt(root, report, 300)) <= 300
    news = next(i for i in items if i["_stream"] == "news_all" and i.get("tickers"))
    assert (
        news["tickers"] and news["_received_at"] and saveticker.report_excerpt(root, news) is None
    )


def test_snapshot_filters_by_asof_and_collects_streams(tmp_path):
    root = _root(tmp_path)
    snap = saveticker.snapshot(root, asof=T_AFTER)
    assert "trends" not in snap  # 트렌드는 2026-10-26 수집 중단 — 남은 옛 파일을 싣지 않는다
    assert {r["tickers"][0] for r in snap["index"]} >= {"QQQ", "SPY"}
    assert len(snap["movers"]["gain"]) == 5 and snap["movers"]["turnover"][0]["turnover"]
    assert snap["community_top5"] and snap["report"]["report_date"] == "2026-10-23"
    assert "S&P500" in snap["report"]["excerpt"] and len(snap["report"]["excerpt"]) <= 1500
    assert all("2026-10-24" <= c["scheduled_at"][:10] <= "2026-11-01" for c in snap["calendar"])
    assert snap["streams_status"]["news_all"] == "partial" and snap["observed_at"]
    # 관측 전 asof 면 화면이 전부 비어 있다(룩어헤드)
    early = saveticker.snapshot(root, asof=T_BEFORE)
    assert early["movers"]["gain"] == [] and early["report"] is None


def test_snapshot_drops_stale_streams(tmp_path):
    """수집기가 멈추면 latest 파일이 '지금' 값처럼 남는다 → 관측 1시간(리포트 하루·캘린더 2주)
    넘으면 뺀다."""
    from datetime import timedelta

    root = _root(tmp_path)
    late = saveticker.snapshot(root, asof=T_AFTER + timedelta(hours=2))
    assert late["movers"]["gain"] == [] and late["community_top5"] == [] and late["index"] == []
    assert late["report"]  # 하루 기준이라 아직 유효
    assert "market_gain_regular" in late["stale"] and "news_reports" not in late["stale"]
    assert saveticker.snapshot(root, asof=T_AFTER)["stale"] is None
    day = saveticker.snapshot(root, asof=T_AFTER + timedelta(days=2))
    assert day["report"] is None and "news_reports" in day["stale"]
    assert not any(s.startswith("calendar_") for s in day["stale"])
    assert saveticker.upcoming(root, asof=T_AFTER + timedelta(days=2), days=9)  # 예정 일정은 유효
    assert saveticker.upcoming(root, asof=T_AFTER + timedelta(days=15), days=9) == []


def test_for_ticker_mentions_and_market_context(tmp_path):
    snap = saveticker.snapshot(_root(tmp_path), asof=T_AFTER)
    qqq = saveticker.for_ticker(snap, "qqq")
    assert "community_top5" in qqq["mentions"] and "trends" not in qqq
    none = saveticker.for_ticker(snap, "005930")
    assert none["mentions"] == {} and none["index"]
    assert saveticker.for_ticker(None, "QQQ") is None


def test_upcoming_calendar_within_seven_days_and_schema_warning(tmp_path, caplog):
    """Phase 21: `upcoming` 은 asof ~ +7일의 경제 일정만(KST 표기), 폴더 없으면 빈 목록.
    schema_version 이 리더 기준과 다르면 경고 1회."""
    import json
    import logging

    root = _root(tmp_path)
    assert saveticker.upcoming(root, asof=T_AFTER) == []  # 샘플 10월 파일엔 10/1 일정만
    up = saveticker.upcoming(root, asof=T_AFTER, days=9)  # 11/2 일정이 창에 든다
    assert up and all(u["kind"] == "macro" and u["source"] == "saveticker" for u in up)
    assert all("2026-10-25" <= u["date"][:10] <= "2026-11-03" for u in up)
    assert up[0]["date"] == "2026-11-02 23:00"  # 14:00 UTC → KST
    assert saveticker.upcoming(None, asof=T_AFTER) == []
    # schema_version 2 파일 → 경고 1회(같은 버전은 반복하지 않음)
    p = root / "data" / "latest" / "community_top5.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data["schema_version"] = 2
    p.write_text(json.dumps(data), encoding="utf-8")
    saveticker._schema_warned.clear()
    with caplog.at_level(logging.WARNING, logger="trader.features.saveticker"):
        saveticker.snapshot(root, asof=T_AFTER)
        saveticker.snapshot(root, asof=T_AFTER)
    assert sum("schema_version 2" in r.message for r in caplog.records) == 1


def test_snapshot_and_upcoming_tolerate_missing_or_broken_status(tmp_path):
    from datetime import timedelta

    root = tmp_path / "saveticker"
    assert (
        saveticker.snapshot(root, asof=T_AFTER) is None
        and saveticker.upcoming(root, asof=T_AFTER) == []
    )
    root.mkdir()
    (root / "status.json").write_text("{", encoding="utf-8")
    assert saveticker.snapshot(root, asof=T_AFTER) is None
    assert saveticker.upcoming(root, asof=T_AFTER) == []
    (root / "data").mkdir()
    (root / "status.json").write_text("{", encoding="utf-8")
    (root / "data" / "status.json").write_text("{", encoding="utf-8")
    snap = saveticker.snapshot(root, asof=T_AFTER)
    assert snap is not None and snap["streams_status"] is None and snap["report"] is None
    assert saveticker.upcoming(root, asof=T_AFTER + timedelta(days=1)) == []


def test_report_excerpt_refuses_path_outside_data(tmp_path):
    root = tmp_path / "saveticker"
    (root / "data" / "attachments").mkdir(parents=True)
    (root / "x.txt").write_text("비밀", encoding="utf-8")
    (tmp_path / "x.txt").write_text("비밀", encoding="utf-8")
    item = {"attachment": {"text_path": "attachments/../../x.txt"}}
    assert saveticker.report_excerpt(root, item) is None
    assert saveticker.report_excerpt(root, {}) is None


def test_ticker_context_preserves_quality_without_claiming_absence(tmp_path):
    from datetime import timedelta

    snap = saveticker.snapshot(_root(tmp_path), asof=T_AFTER + timedelta(hours=2))
    data = saveticker.for_ticker(snap, "QQQ")
    assert data["stale"] == snap["stale"]
    assert data["streams_status"] == snap["streams_status"]
    assert data["data_gaps"]
    assert "없다는 뜻" not in data["note"]


def test_snapshot_market_prevents_us_movers_becoming_kr_candidates(tmp_path):
    kr = saveticker.snapshot(_root(tmp_path), asof=T_AFTER, market="KR")
    assert kr["market"] == "KR"
    assert kr["movers"] == {"gain": [], "loss": [], "turnover": []}
    assert kr["global_context"]["market"] == "US"
    assert kr["global_context"]["index"]
    assert kr["data_gaps"]
    us = saveticker.snapshot(SAMPLE, asof=T_AFTER, market="US")
    assert us["movers"]["gain"]


def test_for_ticker_market_keyword_filters_legacy_snapshot(tmp_path):
    snap = saveticker.snapshot(_root(tmp_path), asof=T_AFTER)
    out = saveticker.for_ticker(snap, "QQQ", market="KR")
    assert out["mentions"] == {}
    assert out["market"] == "KR"
    assert out["index"] == []
    assert out["global_context"]["market"] == "US"
