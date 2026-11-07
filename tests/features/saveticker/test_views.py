"""수집 화면 정의(`views.py`): 확장 수집 계획이 요구 스트림을 모두 담고 작업(lane)을 나눈다."""


def test_all_requested_rankings_and_calendar_months_are_scheduled():
    from trader.features.saveticker.views import build_views

    views = build_views("2026-12", "2027-01")
    streams = {v.stream for v in views}
    assert {
        "community_index_live",
        "community_gain_live",
        "community_loss_live",
        "market_turnover_live",
        "market_gain_live",
        "market_loss_live",
        "calendar_2026-12",
        "calendar_2027-01",
        "community_top5",
    } <= streams
    assert "community_posts" not in streams
    assert all(v.interval_s == 300 for v in views if v.kind == "calendar")
    assert {"market_turnover_regular", "community_gain_regular"} <= streams


def test_lanes_separate_slow_pdf_calendar_from_fast_pages():
    from trader.features.saveticker.views import View, build_views, lane_for

    views = build_views("2026-10", "2026-10") + [
        View("news_all", "news"),
        View("news_reports", "news"),
    ]
    groups = {}
    for view in views:
        groups.setdefault(lane_for(view), []).append(view.stream)
    assert set(groups) == {"news", "community", "market", "background"}
    assert groups["news"] == ["news_all"]
    assert set(groups["background"]) == {"calendar_2026-10", "news_reports"}
    assert len([s for streams in groups.values() for s in streams]) == len(views)
