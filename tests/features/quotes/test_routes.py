"""`/quotes*` 자원: 장중만 KIS, TTL 캐시, 바쁘거나 장 밖이면 스냅샷 값(stale), 국내는 허브 먼저.

옛 `tests/web/test_quotes.py` 13건 이식(이름 유지; 응답 키 `asof` → `at`, 시세마다 `source`,
`hub_yield` 는 허브 `yield_reason`) + `next_poll_ms`·파생 시세 2건."""

import threading
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from tests.features.conftest import web_client
from tests.features.quotes.conftest import NOW, FakeMarket
from trader.features.quotes import routes as quotes_mod
from trader.features.quotes.feature import FEATURE
from trader.kernel.ledger import db

OPEN = datetime(2026, 10, 9, 2, 0, tzinfo=UTC)  # 금 11:00 KST
CLOSED = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)  # 17:00 KST
US_OPEN = datetime(2026, 10, 9, 14, 0, tzinfo=UTC)  # 금 10:00 ET


def _client(project, now, *, hub=None, market="fake"):
    c = web_client(project, [FEATURE], now=now)
    m = FakeMarket() if market == "fake" else market
    quotes_mod.install(project / "state", market=m, hub=hub)
    return c, m


def _snap(project, rows=None):
    conn = db.connect(project / "state" / "trader.db")
    rows = rows or [("paper-main", "2026-10-08", "005930", "70500")]
    for acct, d, t, px in rows:
        conn.execute(
            "INSERT INTO positions_snapshot(account_id, market, date, ticker, qty, avg_price, "
            "current_price, eval_amount, created_at) VALUES (?, 'KR', ?, ?, 1, '70000', ?, ?, "
            "'2026-10-08T07:00:00+00:00')",
            (acct, d, t, px, px),
        )
    conn.commit()
    return conn


def test_quotes_open_session_calls_kis(project):
    c, m = _client(project, OPEN)
    r = c.get("/api/quotes", params={"market": "KR", "tickers": "005930"}).json()
    assert r["session"] == "open"
    q = r["quotes"]["005930"]
    assert q["price"] == "71500" and q["stale"] is False and q["source"] == "rest"
    assert q["at"] == NOW.isoformat()  # KIS 응답 시각
    assert m.calls.count("get_price_kr") == 1


def test_quotes_closed_session_no_kis_call(project):
    c, m = _client(project, CLOSED)
    _snap(project).close()
    r = c.get("/api/quotes", params={"market": "KR", "tickers": "005930,000660"}).json()
    assert r["session"] == "closed"
    assert r["quotes"]["005930"] == {
        "price": "70500",
        "change_pct": None,
        "at": "2026-10-08",
        "stale": True,
        "source": "snapshot",
    }
    assert "000660" not in r["quotes"]
    assert "get_price_kr" not in m.calls


def test_snapshot_prices_batches_latest_per_ticker(project):
    """전체 검토 3차(성능): 종목별 쿼리 대신 한 번에, 종목마다 가장 최근 날짜(같은 날이면 큰 id)."""
    web_client(project, [FEATURE])  # 원장 생성
    conn = _snap(
        project,
        [
            ("a", "2026-10-07", "005930", "69000"),
            ("b", "2026-10-08", "005930", "70500"),
            ("a", "2026-10-08", "000660", "180000"),
            ("a", "2026-10-09", "000660", None),  # 현재가 없는 행은 건너뛴다
        ],
    )
    got = quotes_mod._snapshot_prices(conn, "KR", ["005930", "000660", "035420"])
    assert {t: (v["price"], v["at"]) for t, v in got.items()} == {
        "005930": ("70500", "2026-10-08"),
        "000660": ("180000", "2026-10-08"),
    }
    assert quotes_mod._snapshot_prices(conn, "KR", []) == {}
    conn.close()


def test_quote_cache_drops_old_entries_and_idle_locks():
    quotes_mod._key_lock(("KR", "OLD"))
    quotes_mod._remember(("KR", "OLD"), {"price": "1"}, now=0.0)
    quotes_mod._remember(("KR", "NEW"), {"price": "2"}, now=quotes_mod.KEEP_S + 1)
    assert set(quotes_mod._cache) == {("KR", "NEW")}
    assert ("KR", "OLD") not in quotes_mod._key_locks


def test_quotes_ttl_cache_dedupes(project):
    c, m = _client(project, OPEN)
    for _ in range(3):
        c.get("/api/quotes", params={"market": "KR", "tickers": "005930"})
    assert m.calls.count("get_price_kr") == 1


def test_quotes_rate_busy_returns_stale(project, monkeypatch):
    c, m = _client(project, OPEN)
    _snap(project).close()

    def busy(*a, **k):
        raise HTTPException(status_code=503, detail="busy")

    monkeypatch.setattr(m, "get_price_kr", busy)
    r = c.get("/api/quotes", params={"market": "KR", "tickers": "005930"})
    assert r.status_code == 200
    assert r.json()["quotes"]["005930"]["stale"] is True


def test_quotes_limit_20_and_validation(project):
    c, _ = _client(project, OPEN)
    many = ",".join(f"{i:06d}" for i in range(21))
    assert c.get("/api/quotes", params={"market": "KR", "tickers": many}).status_code == 400
    assert c.get("/api/quotes", params={"market": "XX", "tickers": "005930"}).status_code == 400
    assert c.get("/api/quotes", params={"market": "KR", "tickers": "../x"}).status_code == 400


def test_quotes_us_exchange_suffix(project):
    c, m = _client(project, US_OPEN)
    r = c.get("/api/quotes", params={"market": "US", "tickers": "AAPL:NAS"}).json()
    assert r["quotes"]["AAPL"]["price"] == "227.55"
    assert c.get("/api/quotes", params={"market": "US", "tickers": "AAPL:XXX"}).status_code == 400


def test_quotes_concurrent_misses_call_kis_once(project):
    """두 요청이 동시에 캐시 미스여도 KIS 호출은 한 번(종목별 락 + 다시 보기)."""
    c, m = _client(project, OPEN)
    orig = m.get_price_kr

    def slow(t):
        threading.Event().wait(0.2)
        return orig(t)

    m.get_price_kr = slow
    ths = [
        threading.Thread(
            target=lambda: c.get("/api/quotes", params={"market": "KR", "tickers": "005930"})
        )
        for _ in range(3)
    ]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    assert m.calls.count("get_price_kr") == 1


class FakeHub:
    yielded = None

    def __init__(self, ticks, connected=True):
        self.ticks, self.connected, self.requested = ticks, connected, []

    def request(self, tickers):
        self.requested.append(list(tickers))
        return {t: v for t, v in self.ticks.items() if t in tickers}

    def status(self):
        return {
            "connected": self.connected,
            "subscribed": len(self.ticks),
            "yield_reason": self.yielded,
        }

    def stop(self):
        pass


def test_quotes_kr_uses_ws_hub_first(project):
    """웹소켓 틱이 있는 종목은 허브 값, 없는 종목만 REST. stream 이면 화면이 1초 주기."""
    tick = {"price": "71600", "change_pct": 1.2, "at": "x", "stale": False, "source": "ws"}
    hub = FakeHub({"000660": tick})
    c, m = _client(project, OPEN, hub=hub)
    r = c.get("/api/quotes", params={"market": "KR", "tickers": "005930,000660"}).json()
    assert r["quotes"]["000660"] == tick and r["stream"] is True and r["source"] == "ws"
    assert r["quotes"]["005930"]["price"] == "71500"  # REST
    assert r["quotes"]["005930"]["source"] == "rest"
    assert m.calls.count("get_price_kr") == 1
    assert hub.requested == [["005930", "000660"]]


def test_quotes_us_and_closed_never_touch_hub(project):
    hub = FakeHub({})
    c, _ = _client(project, OPEN, hub=hub)
    r = c.get("/api/quotes", params={"market": "US", "tickers": "AAPL"}).json()
    assert r["stream"] is False and hub.requested == []
    quotes_mod.reset()
    hub2 = FakeHub({})
    c2, _ = _client(project, CLOSED, hub=hub2)
    assert (
        c2.get("/api/quotes", params={"market": "KR", "tickers": "005930"}).json()["stream"]
        is False
    )
    assert hub2.requested == []


class BookHub(FakeHub):
    def request_indices(self):
        return {"KOSPI": {"price": "2650.12", "change_pct": -0.0085, "at": "x"}}

    def request_book(self, ticker):
        return {
            "asks": [["71100", "10"]],
            "bids": [["71000", "5"]],
            "total_ask": "10",
            "total_bid": "5",
            "time": "093015",
            "at": "x",
        }


def test_indices_and_book_routes(project):
    c, m = _client(project, OPEN, hub=BookHub({}))
    r = c.get("/api/quotes/indices").json()
    assert r["session"] == "open" and r["indices"]["KOSPI"]["price"] == "2650.12"
    # 허브에 틱이 없는 KOSDAQ 은 REST 로 채운다
    assert r["indices"]["KOSDAQ"]["source"] == "rest" and m.calls.count("get_index_kr") == 1
    b = c.get("/api/quotes/book", params={"ticker": "005930"}).json()
    assert b["book"]["asks"][0] == ["71100", "10"]
    assert c.get("/api/quotes/book", params={"ticker": "../x"}).status_code == 400
    quotes_mod.reset()
    c2, _ = _client(project, CLOSED, hub=BookHub({}))
    assert c2.get("/api/quotes/indices").json() == {
        "session": "closed",
        "at": CLOSED.isoformat(),
        "indices": {},
        "next_poll_ms": 60000,
    }


def test_book_falls_back_to_rest_without_hub(project):
    c, m = _client(project, OPEN)
    b = c.get("/api/quotes/book", params={"ticker": "005930"}).json()
    assert b["book"]["asks"] == [["71100", "10"]] and b["book"]["source"] == "rest"
    assert m.calls == ["get_orderbook_kr"]


def test_quotes_reports_hub_yield_reason(project):
    """2-P13: 허브가 양보 중이면 응답에 사유를 실어 화면이 REST 폴링의 이유를 보인다."""
    hub = FakeHub({}, connected=False)
    hub.yielded = "ws_session_busy"
    c, _ = _client(project, OPEN, hub=hub)
    r = c.get("/api/quotes", params={"market": "KR", "tickers": "005930"}).json()
    assert r["stream"] is False and r["hub_yield"] == "ws_session_busy" and r["source"] == "rest"
    assert (
        c.get("/api/quotes", params={"market": "US", "tickers": "AAPL"}).json()["hub_yield"] is None
    )


def test_quotes_next_poll_ms_by_session(project):
    """국내 정규장 1000(허브), 미국 정규장 5000(settings.quotes.us_poll_ms), 장외 60000."""
    c, _ = _client(project, OPEN, hub=FakeHub({}))
    kr = c.get("/api/quotes", params={"market": "KR", "tickers": "005930"}).json()
    assert kr["next_poll_ms"] == 1000
    off = c.get("/api/quotes", params={"market": "US", "tickers": "AAPL"}).json()
    assert off["session"] == "closed" and off["next_poll_ms"] == 60000
    quotes_mod.reset()
    c2, _ = _client(project, US_OPEN)
    us = c2.get("/api/quotes", params={"market": "US", "tickers": "AAPL"}).json()
    assert us["session"] == "open" and us["next_poll_ms"] == 5000
    kr_off = c2.get("/api/quotes", params={"market": "KR", "tickers": "005930"}).json()
    assert kr_off["next_poll_ms"] == 60000


def test_fo_quotes_rest_and_poll(project):
    """파생 시세는 REST 만: 종류는 상품 설정 접두로, 정규장 2000(fo_poll_ms)·장외 60000."""
    c, m = _client(project, OPEN)
    r = c.get("/api/quotes/fo", params={"codes": "A05612,B05612345"}).json()
    assert r["next_poll_ms"] == 2000 and r["session"] == "open"
    assert r["quotes"]["A05612"]["price"] == "350.25" and r["quotes"]["A05612"]["kind"] == "FUTURES"
    assert r["quotes"]["B05612345"]["kind"] == "CALL"
    assert r["kospi200"]["price"] == "351.10"
    assert "get_fo_price:FUTURES" in m.calls and "get_fo_price:CALL" in m.calls
    assert c.get("/api/quotes/fo", params={"codes": "ZZZ999"}).status_code == 400
    quotes_mod.reset()
    c2, m2 = _client(project, CLOSED)
    off = c2.get("/api/quotes/fo", params={"codes": "A05612"}).json()
    assert off["next_poll_ms"] == 60000 and off["quotes"] == {} and m2.calls == []


def test_no_market_client_returns_empty_shapes(project):
    """공용 실전 키가 없으면(시세 클라이언트 없음) 오류 대신 빈 값·스냅샷."""
    c, _ = _client(project, OPEN, market=None)
    r = c.get("/api/quotes", params={"market": "KR", "tickers": "005930"})
    assert r.status_code == 200 and r.json()["quotes"] == {}
    assert c.get("/api/quotes/book", params={"ticker": "005930"}).json()["book"] is None
    assert c.get("/api/quotes/indices").json()["indices"] == {}


def test_runtime_without_real_key_has_no_market(project):
    """설치하지 않으면 시크릿에서 직접 만든다 — 공용 실전 키가 없으면 시세 클라이언트·허브 없음."""
    c = web_client(project, [FEATURE], now=OPEN)
    r = c.get("/api/quotes", params={"market": "KR", "tickers": "005930"})
    assert r.status_code == 200 and r.json()["quotes"] == {}
    rt = quotes_mod.runtime_for(c.app.state.deps)
    assert rt.market is None and rt.hub is None
    assert quotes_mod.runtime_for(c.app.state.deps) is rt  # 프로세스에 한 번


@pytest.mark.parametrize("pair", ["EURKRW"])
def test_fx_unknown_pair_400(project, pair):
    c, _ = _client(project, OPEN)
    assert c.get("/api/quotes/fx", params={"pair": pair}).status_code == 400
