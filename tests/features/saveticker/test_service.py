from datetime import UTC, datetime


def test_all_requested_rankings_and_calendar_months_are_scheduled():
    from trader.features.saveticker.service import build_views

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


def test_one_view_failure_does_not_skip_other_views(tmp_path):
    from trader.features.saveticker.models import Capture
    from trader.features.saveticker.service import View, collect_cycle
    from trader.features.saveticker.store import Store

    class Browser:
        def collect(self, view):
            if view.stream == "news_all":
                raise RuntimeError("private-secret-should-not-appear")
            return Capture(
                stream=view.stream,
                page_url="https://www.saveticker.com/watchlist",
                observed_at=datetime.now(UTC),
                items=[{"id": "x", "title": "종목"}],
            )

    store = Store(tmp_path)
    results = collect_cycle(
        Browser(), store, [View("news_all", "news"), View("market_gain_live", "market")]
    )
    assert [r["status"] for r in results] == ["error", "ok"]
    assert "private-secret" not in (tmp_path / "status.json").read_text(encoding="utf-8")


def test_virtual_scroll_accumulates_recycled_rows_and_marks_limit():
    from trader.features.saveticker.models import Item
    from trader.features.saveticker.service import accumulate

    batches = iter(
        [
            [Item(id="a", title="첫 행")],
            [Item(id="b", title="다음 행")],
            [Item(id="c", title="마지막 행")],
        ]
    )
    rows, reason = accumulate(lambda: next(batches), lambda: None, max_steps=2)
    assert {x.id for x in rows} == {"a", "b"}
    assert reason == "scroll_limit"


def test_stable_window_never_claims_whole_site_complete():
    from trader.features.saveticker.models import Item
    from trader.features.saveticker.service import accumulate

    rows, reason = accumulate(lambda: [Item(id="a", title="행")], lambda: None, max_steps=8)
    assert len(rows) == 1
    assert reason == "no_progress_unverified_end"


def test_hot_list_refresh_has_priority_but_background_is_not_starved():
    from trader.features.saveticker.service import View, choose_due

    views = [
        View("calendar_2026-10", "calendar", interval_s=300),
        View("news_all", "news", interval_s=30),
    ]
    due = {v.stream: 0 for v in views}
    assert choose_due(views, due, now=100, hot_streak=0).stream == "news_all"
    assert choose_due(views, due, now=100, hot_streak=8).stream == "calendar_2026-10"
    assert choose_due(views, {v.stream: 200 for v in views}, now=100) is None


def test_lanes_separate_slow_pdf_calendar_from_fast_pages():
    from trader.features.saveticker.service import View, build_views, lane_for

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
