"""quotes 테스트 공용: 가짜 웹소켓 연결·가짜 시세 클라이언트·런타임 초기화(네트워크 없음)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from trader.features.quotes import routes
from trader.features.quotes.hub import QuoteHub

NOW = datetime(2026, 10, 27, 1, 0, tzinfo=UTC)  # 화 10:00 KST


class FakeConn:
    def __init__(self):
        self.sent: list[dict | str] = []
        self.inbox: list[str] = []
        self.closed = False

    def send(self, text):
        try:
            self.sent.append(json.loads(text))
        except ValueError:
            self.sent.append(text)

    def recv(self, timeout=None):
        if self.inbox:
            return self.inbox.pop(0)
        raise TimeoutError

    def close(self):
        self.closed = True

    def subs(self):
        """등록(1)·해제(2) 순서대로 (tr_type, 종목)."""
        return [
            (m["header"]["tr_type"], m["body"]["input"]["tr_key"])
            for m in self.sent
            if isinstance(m, dict) and "body" in m
        ]


class KeyMarket:
    """허브가 쓰는 접속키 면만 가진 가짜 시세 클라이언트."""

    def __init__(self, keys):
        self.keys, self.invalidated = keys, []

    def approval_key(self):
        return self.keys[len(self.invalidated)]

    def invalidate_approval(self):
        self.invalidated.append(1)


def make_hub(*, is_open=True, conns=None, keys=None, busy=None):
    clock = [0.0]
    conns = conns if conns is not None else [FakeConn()]
    made = []
    market = KeyMarket(keys or ["ak-1", "ak-2", "ak-3"])

    def connect(url):
        if not conns:
            raise OSError("연결 거부")
        c = conns.pop(0)
        if isinstance(c, Exception):
            raise c
        made.append(c)
        return c

    hub = QuoteHub(
        market,
        ws_connect=connect,
        now=lambda: NOW,
        busy=busy or (lambda: False),
        clock=lambda: clock[0],
        is_open=lambda now: is_open,
        autostart=False,
    )
    return hub, clock, made, market.invalidated


class FakeMarket:
    """REST 시세 가짜: 호출 이름을 calls 에 남긴다."""

    def __init__(self):
        self.calls: list[str] = []

    def _q(self, ticker, market, price, pct):
        return SimpleNamespace(
            ticker=ticker, market=market, price=Decimal(price), change_pct=pct, asof=NOW
        )

    def get_price_kr(self, ticker):
        self.calls.append("get_price_kr")
        return self._q(ticker, "KR", "71500", 0.01)

    def get_price_us(self, symbol, exchange):
        self.calls.append("get_price_us")
        return self._q(symbol, "US", "227.55", -0.002)

    def get_index_kr(self, code="0001"):
        self.calls.append("get_index_kr")
        return self._q(code, "KR", "2650.12", 0.005)

    def get_orderbook_kr(self, ticker):
        self.calls.append("get_orderbook_kr")
        return SimpleNamespace(
            ticker=ticker,
            market="KR",
            asks=[(Decimal("71100"), 10)],
            bids=[(Decimal("71000"), 5)],
            asof=NOW,
        )

    def get_fo_price(self, code, kind):
        self.calls.append(f"get_fo_price:{kind}")
        return SimpleNamespace(
            code=code,
            kind=kind,
            price=Decimal("350.25"),
            bid=Decimal("350.20"),
            ask=Decimal("350.30"),
            change_pct=0.004,
            quote_asof=NOW,
            received_at=NOW,
        )

    def get_kospi200(self):
        self.calls.append("get_kospi200")
        return Decimal("351.10"), NOW


@pytest.fixture(autouse=True)
def _reset_runtime():
    """모듈 전역 런타임·REST 캐시가 테스트 사이에 새지 않게 앞뒤로 비운다(허브는 멈춘다)."""
    routes.reset()
    yield
    routes.reset()
