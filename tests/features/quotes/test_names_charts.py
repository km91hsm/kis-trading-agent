"""quotes `/names*`·`/charts*`: 종목명(원장 → 캐시 JSON, 네트워크 없음)·검색, 일봉 + 체결 마커 +
손절·목표 선, 당일 분봉(종목별 락·캐시).

옛 `tests/web/test_names_api.py` 2건·`test_runs_charts.py` 차트 부분 이식. 마커는 레인 import 없이
`orders`/`fills` 표에서, 선 값은 `decisions.data_json` 의 `stop_loss/take_profit` 에서 읽는다."""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from tests.features.conftest import web_client
from trader.features.quotes import charts
from trader.features.quotes import routes as quotes_mod
from trader.features.quotes.feature import FEATURE
from trader.kernel.broker.errors import KisApiError
from trader.kernel.broker.fake import FakeMarket
from trader.kernel.broker.models import Candle
from trader.kernel.ledger import db

NOW = datetime(2026, 10, 9, 6, 0, tzinfo=UTC)  # 금 15:00 KST
D0 = date(2026, 8, 20)


def _daily(n=30):
    return [
        Candle(date=D0 + timedelta(days=i), open=Decimal(100), high=Decimal(110),
               low=Decimal(90), close=Decimal(105), volume=1000)
        for i in range(n)
    ]  # fmt: skip


def _minutes(d=date(2026, 10, 9), start="0900", n=6):
    h, m = int(start[:2]), int(start[2:])
    return [
        Candle(date=d, open=Decimal(1), high=Decimal(2), low=Decimal(1), close=Decimal(2),
               volume=10, time=f"{h:02d}{m + i:02d}00")
        for i in range(n)
    ]  # fmt: skip


def _client(project, market="fake"):
    c = web_client(project, [FEATURE], now=NOW)
    if market == "fake":
        market = FakeMarket(
            candles={"005930": _daily(), "AAPL": _daily(5)},
            minutes={"005930": _minutes(), "AAPL": _minutes(start="0900", n=5)},
        )
    quotes_mod.install(project / "state", market=market)
    return c, market


def _conn(project):
    return db.connect(project / "state" / "trader.db")


def _decision(conn, d="2026-10-08", ticker="005930", stop="65000", take="80000"):
    data = {"action": "BUY", "stop_loss": stop, "take_profit": take}
    cur = conn.execute(
        "INSERT INTO decisions(date, market, ticker, action, data_json, created_at) "
        "VALUES (?, 'KR', ?, 'BUY', ?, ?)",
        (d, ticker, json.dumps(data), NOW.isoformat()),
    )
    conn.commit()
    return cur.lastrowid


def _order_fill(conn, filled_at, *, account="paper-main", ticker="005930", market="KR",
                side="BUY", status="filled"):  # fmt: skip
    cur = conn.execute(
        "INSERT INTO orders(account_id, ticker, market, side, qty, order_type, price, status, "
        "mode, created_at) VALUES (?, ?, ?, ?, 3, 'LIMIT', '71000', ?, 'auto', ?)",
        (account, ticker, market, side, status, filled_at),
    )
    conn.execute(
        "INSERT INTO fills(order_id, account_id, qty, price, filled_at) VALUES (?, ?, 3, ?, ?)",
        (cur.lastrowid, account, "70900", filled_at),
    )
    conn.commit()


# --- names -----------------------------------------------------------------------


def test_names_from_ledger_and_cache(project):
    c, _ = _client(project)
    conn = _conn(project)
    conn.execute(
        "INSERT INTO ticker_names(market, ticker, name, source, updated_at) "
        "VALUES ('KR', '005930', '가나전자', 'balance', 'x')"
    )
    conn.commit()
    conn.close()
    cache = project / "state" / "cache"
    cache.mkdir(parents=True)
    (cache / "edgar_tickers.json").write_text(
        json.dumps({"fetched_at": "2020-01-01T00:00:00+00:00",
                    "map": {"AAPL": {"cik": "1", "name": "Fake Apple Co"}}}),
        encoding="utf-8",
    )  # fmt: skip
    assert c.get("/api/names").json() == {"KR:005930": "가나전자"}
    r = c.get("/api/names", params={"market": "US", "tickers": "AAPL,ZZZZ"})
    assert r.status_code == 200 and r.json() == {"US:AAPL": "Fake Apple Co"}
    r = c.get("/api/names", params={"market": "KR", "tickers": "005930"})
    assert r.json() == {"KR:005930": "가나전자"}
    # 웹은 읽기 전용: 캐시 적중을 원장에 쓰지 않는다(옛 동작과 다름 — README)
    assert c.get("/api/names").json() == {"KR:005930": "가나전자"}


def test_names_rejects_bad_market_and_ticker(project):
    c, _ = _client(project)
    assert c.get("/api/names", params={"market": "JP", "tickers": "A"}).status_code == 400
    assert c.get("/api/names", params={"market": "KR", "tickers": "../x"}).status_code == 400
    assert c.get("/api/names", params={"tickers": "005930"}).status_code == 400  # market 필수


def test_names_search_ranks_held_and_watch_first(project):
    c, _ = _client(project)
    conn = _conn(project)
    conn.executemany(
        "INSERT INTO ticker_names(market, ticker, name, source, updated_at) VALUES (?,?,?,?,'x')",
        [("KR", "005930", "가나전자", "balance"), ("KR", "000660", "가나하이", "balance"),
         ("KR", "035420", "다라", "balance")],
    )  # fmt: skip
    conn.execute(
        "INSERT INTO positions_snapshot(account_id, market, date, ticker, qty, avg_price, "
        "current_price, eval_amount, created_at) VALUES ('paper-main', 'KR', '2026-10-08', "
        "'000660', 1, '1', '1', '1', 'x')"
    )
    conn.execute(
        "INSERT INTO control(key, value, updated_at) VALUES ('watchlist', '[\"KR:035420\"]', 'x')"
    )
    conn.commit()
    conn.close()
    got = c.get("/api/names/search", params={"q": "가나"}).json()
    assert [(x["ticker"], x["source"]) for x in got] == [("000660", "held"), ("005930", "ledger")]
    assert c.get("/api/names/search", params={"q": "다라"}).json()[0]["source"] == "watch"
    assert c.get("/api/names/search", params={"q": " "}).json() == []
    assert c.get("/api/names/search", params={"q": "가", "market": "JP"}).status_code == 400
    assert c.get("/api/names/search", params={"q": "가나", "market": "US"}).json() == []


# --- 일봉 -------------------------------------------------------------------------


def test_charts_candles_fill_markers_and_lines(project):
    c, m = _client(project)
    conn = _conn(project)
    _decision(conn, d="2026-10-07", stop="60000", take="75000")
    _decision(conn, d="2026-10-08")  # 최신 결정이 선 값
    _order_fill(conn, "2026-10-08T01:00:00+00:00")
    _order_fill(conn, "2026-10-08T02:00:00+00:00", ticker="000660")  # 다른 종목
    conn.close()
    r = c.get("/api/charts", params={"ticker": "005930", "market": "KR", "days": 120}).json()
    assert len(r["candles"]) == 30 and r["candles"][0]["time"] == "2026-08-20"
    assert isinstance(r["candles"][0]["close"], str)
    assert [(x["kind"], x["time"], x["side"]) for x in r["markers"]] == [
        ("fill", "2026-10-08", "BUY")
    ]
    assert "paper-main" in r["markers"][0]["text"] and "70900" in r["markers"][0]["text"]
    assert r["lines"] == {"stop_loss": "65000", "take_profit": "80000", "date": "2026-10-08"}
    assert m.calls.count("get_daily_kr") == 1


def test_charts_lines_empty_without_decision(project):
    c, _ = _client(project)
    r = c.get("/api/charts", params={"ticker": "005930", "market": "KR"}).json()
    assert r["markers"] == [] and r["lines"] == {
        "stop_loss": None, "take_profit": None, "date": None
    }  # fmt: skip


def test_charts_validation_and_no_market_client_503(project):
    c, _ = _client(project)
    assert c.get("/api/charts", params={"ticker": "../x", "market": "KR"}).status_code == 400
    assert c.get("/api/charts", params={"ticker": "005930", "market": "JP"}).status_code == 400
    bad_ex = {"ticker": "AAPL", "market": "US", "exchange": "XXX"}
    assert c.get("/api/charts", params=bad_ex).status_code == 400
    ok = c.get("/api/charts", params={"ticker": "AAPL", "market": "US", "exchange": "NAS"})
    assert ok.status_code == 200 and len(ok.json()["candles"]) == 5
    # 거래소를 안 주면 유니버스 → 없으면 NAS
    assert c.get("/api/charts", params={"ticker": "AAPL", "market": "US"}).status_code == 200
    quotes_mod.install(project / "state", market=None)
    assert c.get("/api/charts", params={"ticker": "005930", "market": "KR"}).status_code == 503


def test_charts_kis_error_is_502(project):
    class Boom(FakeMarket):
        def get_daily_kr(self, ticker, days=250):
            raise KisApiError("EGW00201", "초당 거래건수 초과")

    c, _ = _client(project, market=Boom())
    r = c.get("/api/charts", params={"ticker": "005930", "market": "KR"})
    assert r.status_code == 502 and "시세 조회 실패" in r.json()["detail"]


def test_charts_days_bounds_and_cache(project):
    c, m = _client(project)
    assert c.get("/api/charts?ticker=005930&market=KR&days=0").status_code == 422
    assert c.get("/api/charts?ticker=005930&market=KR&days=5000").status_code == 422
    assert c.get("/api/charts?ticker=005930&market=KR&days=60").status_code == 200
    assert c.get("/api/charts?ticker=005930&market=KR&days=60").status_code == 200
    assert m.calls.count("get_daily_kr") == 1  # 같은 날·같은 키는 캐시


# --- 분봉 -------------------------------------------------------------------------


def test_minute_chart_epoch_and_markers_same_day_only(project):
    c, _ = _client(project)
    conn = _conn(project)
    _order_fill(conn, "2026-10-09T00:02:30+00:00")  # 09:02 KST 오늘
    _order_fill(conn, "2026-10-08T00:02:30+00:00")  # 어제 → 제외
    conn.close()
    r = c.get("/api/charts/minute?ticker=005930&market=KR&count=5").json()
    assert r["interval"] == "1m" and r["tz"] == "Asia/Seoul" and r["session_date"] == "2026-10-09"
    t0 = int(datetime(2026, 10, 9, 0, 1, tzinfo=UTC).timestamp())  # 6개 중 최신 5개(09:01~)
    assert [x["time"] for x in r["candles"]] == [t0 + 60 * i for i in range(5)]
    assert isinstance(r["candles"][0]["close"], str) and "date" not in r["candles"][0]
    assert [(x["kind"], x["time"]) for x in r["markers"]] == [("fill", t0 + 60)]
    assert r["markers"][0]["text"].startswith("09:02 paper-main BUY")  # 현지 시각 접두사


def test_minute_chart_markers_local_day_bounds_in_sql(project):
    """그날(KST) 경계를 SQL 로 — 23:59:59 KST 는 포함, 다음 날 00:00 KST 는 제외."""
    c, _ = _client(project)
    conn = _conn(project)
    _order_fill(conn, "2026-10-09T14:59:59+00:00")  # 23:59 KST 오늘
    _order_fill(conn, "2026-10-09T15:00:00+00:00")  # 10-10 00:00 KST
    _order_fill(conn, "2026-10-08T14:59:59+00:00")  # 10-08 23:59 KST
    conn.close()
    r = c.get("/api/charts/minute?ticker=005930&market=KR&count=6").json()
    assert [x["text"][:5] for x in r["markers"]] == ["23:59"]


def test_minute_chart_concurrent_misses_call_kis_once(project):
    class Slow(FakeMarket):
        def get_minute_kr(self, ticker, *, count=30):
            time.sleep(0.2)
            return super().get_minute_kr(ticker, count=count)

    c, m = _client(project, market=Slow(minutes={"005930": _minutes()}))
    codes: list[int] = []
    ts = [
        threading.Thread(
            target=lambda: codes.append(
                c.get("/api/charts/minute?ticker=005930&market=KR&count=4").status_code
            )
        )
        for _ in range(3)
    ]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert codes == [200, 200, 200]
    assert m.calls.count("get_minute_kr") == 1


def test_minute_chart_us_epoch_uses_new_york(project):
    c, _ = _client(project)
    r = c.get("/api/charts/minute?ticker=AAPL&market=US&exchange=NAS&count=2").json()
    # 가짜 분봉 5개(09:00~09:04) 중 최신 2개 → 첫 봉 09:03 뉴욕(EDT, UTC−4) = 13:03Z
    assert r["tz"] == "America/New_York"
    assert r["candles"][0]["time"] == int(datetime(2026, 10, 9, 13, 3, tzinfo=UTC).timestamp())


def test_minute_cache_and_reset(project):
    c, m = _client(project)
    for _ in range(2):
        assert c.get("/api/charts/minute?ticker=005930&market=KR&count=3").status_code == 200
    assert m.calls.count("get_minute_kr") == 1
    charts.reset()
    assert c.get("/api/charts/minute?ticker=005930&market=KR&count=3").status_code == 200
    assert m.calls.count("get_minute_kr") == 2
