"""수집 화면(View) 정의와 작업(lane) 배정. Native host 가 확장에 넘길 수집 계획을 만든다."""

from __future__ import annotations

from dataclasses import dataclass

from trader.features.saveticker.dom import months_between

PATHS = {
    "news": "/news",
    "community": "/community",
    "market": "/watchlist",
    "calendar": "/calendar",
}


@dataclass(frozen=True)
class View:
    stream: str
    kind: str
    tab: str | None = None
    source: str | None = None
    category: str | None = None
    month: str | None = None
    regular: bool = False
    interval_s: float = 20
    ticker: str | None = None

    @property
    def url(self) -> str:
        return "https://www.saveticker.com" + PATHS[self.kind]


def lane_for(view: View) -> str:
    if view.kind == "calendar" or view.stream == "news_reports":
        return "background"
    return view.kind


def build_views(start_month: str, end_month: str) -> list[View]:
    views = [View("community_top5", "community")]
    for kind, choices in [
        ("community", [("index", "지수"), ("gain", "상승"), ("loss", "하락")]),
        ("market", [("turnover", "거래대금"), ("gain", "상승"), ("loss", "하락")]),
    ]:
        for slug, label in choices:
            for regular in (False, True):
                basis = "regular" if regular else "live"
                views.append(
                    View(
                        f"{kind}_{slug}_{basis}",
                        kind,
                        tab=label,
                        regular=regular,
                        interval_s=300 if regular else 20,
                    )
                )
    for month in months_between(start_month, end_month):
        views.append(View(f"calendar_{month}", "calendar", month=month, interval_s=300))
    return views
