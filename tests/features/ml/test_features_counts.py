from datetime import UTC, datetime

import pandas as pd

from tests.features.ingest.helpers import FakeDart, FakeMarket
from trader.features.ingest.api import Universe
from trader.features.ml import features
from trader.kernel.config.time import KST
from trader.kernel.session.run_store import run_dir, write_checkpoint

T0 = datetime(2026, 10, 17, 6, 0, tzinfo=UTC)


def test_snapshot_fills_counts_from_inputs(project):
    # 건수는 행의 날짜(마지막 완성 봉)의 운영일 폴더에서 센다
    last_day = FakeMarket().get_daily_kr("005930", days=300)[-1].date
    d = run_dir(last_day, "KR", "005930")
    write_checkpoint(
        d,
        "00_inputs",
        {
            "meta": {"ticker": "005930"},
            "news_macro": {
                "news": {
                    "data": {"stock": [{"t": 1}, {"t": 2}, {"t": 3}], "macro": [], "sources": {}},
                    "received_at": "",
                    "error": None,
                }
            },
            "sentiment": {
                "community": {
                    "data": {"posts": [{"p": 1}], "sources": {}},
                    "received_at": "",
                    "error": None,
                }
            },
        },
    )
    n = features.snapshot(
        market_client=FakeMarket(),
        dart=FakeDart(),
        universe=Universe(kr=["005930"]),
        markets=["KR"],
        asof=T0,
        now=lambda: T0,
    )
    assert n == 1
    got = pd.read_parquet(next((project / "state" / "features").glob("*.parquet")))
    assert got.iloc[0]["news_count"] == 3 and got.iloc[0]["community_count"] == 1
    assert features.counts_from_inputs("000660", "KR", T0) == (None, None)  # 00_inputs 없음


def test_news_count_reads_the_shape_ingest_actually_writes(project):
    """수집(`ingest.news.collect_news`)이 쓰는 뉴스 절은 `{"stock", "macro", "sources"}` 다 — 건수
    계산이 다른 키(`items`)를 찾아 `news_count` 피처가 실사용에서 전부 NaN 이었다. 종목 뉴스
    (`stock`)만 세고 시장 공통 뉴스(`macro`)는 세지 않는다."""
    from trader.features.ingest import news as ingest_news
    from trader.features.ingest.settings import NewsCfg

    cfg = NewsCfg(google_rss=False, naver=False)
    empty = ingest_news.collect_news(cfg, {}, ticker="005930", name="x", market="KR", asof=T0)
    assert set(empty) == {"stock", "macro", "sources"}  # 건수 계산이 기대는 모양
    d = run_dir(T0.astimezone(KST).date(), "KR", "005930")
    data = {**empty, "stock": [{"t": 1}, {"t": 2}], "macro": [{"m": i} for i in range(5)]}
    write_checkpoint(
        d, "00_inputs", {"news_macro": {"news": {"data": data, "received_at": "", "error": None}}}
    )
    assert features.counts_from_inputs("005930", "KR", T0) == (2, None)


def test_counts_come_from_the_candle_day_not_the_snapshot_morning(project):
    """일일 스냅샷은 다음 날 06:30 에 돈다 — 그 시각의 운영일 폴더에는 아직 분석이 없어 건수가 늘
    비었다(KR). 행의 날짜(마지막 완성 봉)의 수집 자료에서 센다. 주말에 다시 돌아도 금요일 행의
    건수가 None 으로 덮이지 않는다."""
    from datetime import date

    friday = date(2026, 10, 16)
    d = run_dir(friday, "KR", "005930")
    data = {"stock": [{"t": 1}, {"t": 2}], "macro": [], "sources": {}}
    write_checkpoint(
        d, "00_inputs", {"news_macro": {"news": {"data": data, "received_at": "", "error": None}}}
    )
    sunday_morning = datetime(2026, 10, 17, 21, 30, tzinfo=UTC)  # 일 06:30 KST
    assert features.counts_from_inputs("005930", "KR", sunday_morning) == (None, None)
    assert features.counts_from_inputs("005930", "KR", sunday_morning, day=friday) == (2, None)
