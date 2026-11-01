"""테스트·dry-run 용 가짜 브로커/시세. KisMarket·KisBroker 와 같은 메서드를 가진다."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal

from trader.kernel.broker.errors import KisApiError
from trader.kernel.broker.models import (
    BrokerAccount,
    Candle,
    Cash,
    Fill,
    FoQuote,
    Market,
    OrderBook,
    OrderRequest,
    OrderResult,
    Position,
    Quote,
)


class FakeMarket:
    def __init__(
        self,
        prices: dict[str, Decimal] | None = None,
        candles: dict[str, list[Candle]] | None = None,
        holidays: set[date] | None = None,
        *,
        minutes: dict[str, list[Candle]] | None = None,
        fail: set[str] | None = None,
        industries: dict[str, str] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        flows: dict[str, list] | None = None,
        ranks: dict[str, list] | None = None,
        fo_quotes: dict[str, FoQuote] | None = None,
        fo_candles: dict[str, list[Candle]] | None = None,
        fo_minutes: dict[str, list[Candle]] | None = None,
        fo_orderbooks: dict[str, OrderBook] | None = None,
        kospi200: tuple[Decimal, datetime | None] | None = None,
    ) -> None:
        # Phase 26 파생 시세
        self.fo_quotes = fo_quotes or {}
        self.fo_candles = fo_candles or {}
        self.fo_minutes = fo_minutes or {}
        self.fo_orderbooks = fo_orderbooks or {}
        self.kospi200 = kospi200
        self.ranks = ranks or {}  # "volume"|"fluct"|"flow" → [RankRow] (Phase 25)
        self.prices = prices or {}
        self.flows = flows or {}  # ticker → [InvestorFlow] (날짜 오름차순)
        self.industries = industries or {}
        self.candles = candles or {}
        self.minutes = minutes or {}
        self.holidays = holidays or set()
        self.fail = fail or set()
        self.calls: list[str] = []
        self._now = now

    def _record(self, name: str) -> None:
        self.calls.append(name)
        if name in self.fail:
            raise KisApiError("FAKE", f"가짜 실패 주입: {name}")

    def volume_rank_kr(self, limit: int = 30) -> list:
        self._record("volume_rank_kr")
        return list(self.ranks.get("volume", []))[:limit]

    def fluctuation_rank_kr(self, limit: int = 30) -> list:
        self._record("fluctuation_rank_kr")
        return list(self.ranks.get("fluct", []))[:limit]

    def foreign_institution_kr(self, limit: int = 30) -> list:
        self._record("foreign_institution_kr")
        return list(self.ranks.get("flow", []))[:limit]

    def get_industry_kr(self, ticker: str) -> str | None:
        self._record("get_industry_kr")
        return self.industries.get(ticker)

    def get_industry_us(self, symbol: str, exchange: str) -> str | None:
        self._record("get_industry_us")
        return self.industries.get(symbol)

    def _quote(self, ticker: str, market: Market) -> Quote:
        if ticker not in self.prices:
            raise KisApiError("FAKE", f"가격 없음: {ticker}")
        return Quote(
            ticker=ticker,
            market=market,
            price=self.prices[ticker],
            change_pct=0.0,
            volume=0,
            asof=self._now(),
        )

    def get_price_kr(self, ticker: str) -> Quote:
        self._record("get_price_kr")
        return self._quote(ticker, "KR")

    def get_price_us(self, symbol: str, exchange: str) -> Quote:
        self._record("get_price_us")
        return self._quote(symbol, "US")

    def get_investor_kr(self, ticker: str, days: int = 20) -> list:
        self._record("get_investor_kr")
        return list(self.flows.get(ticker, []))[-days:]

    def get_daily_kr(self, ticker: str, days: int = 250) -> list[Candle]:
        self._record("get_daily_kr")
        return list(self.candles.get(ticker, []))[-days:]

    def get_daily_us(self, symbol: str, exchange: str, days: int = 250) -> list[Candle]:
        self._record("get_daily_us")
        return list(self.candles.get(symbol, []))[-days:]

    def get_minute_kr(self, ticker: str, *, count: int = 30) -> list[Candle]:
        self._record("get_minute_kr")
        return list(self.minutes.get(ticker, []))[-count:]

    def get_minute_us(self, symbol: str, exchange: str, *, count: int = 120) -> list[Candle]:
        self._record("get_minute_us")
        return list(self.minutes.get(symbol, []))[-count:]

    def is_holiday(self, d: date) -> bool:
        self._record("is_holiday")
        return d in self.holidays

    # --- 파생(Phase 26) ---

    def get_fo_price(self, code: str, kind: str) -> FoQuote:
        self._record("get_fo_price")
        if code not in self.fo_quotes:
            raise KisApiError("FAKE", f"파생 시세 없음: {code}")
        return self.fo_quotes[code]

    def get_fo_orderbook(self, code: str, kind: str) -> OrderBook:
        self._record("get_fo_orderbook")
        if code not in self.fo_orderbooks:
            return OrderBook(ticker=code, market="KR", asks=[], bids=[], asof=self._now())
        return self.fo_orderbooks[code]

    def get_fo_daily(self, code: str, kind: str, *, days: int = 100) -> list[Candle]:
        self._record("get_fo_daily")
        return list(self.fo_candles.get(code, []))[-days:]

    def get_fo_minute(self, code: str, kind: str, *, count: int = 60) -> list[Candle]:
        self._record("get_fo_minute")
        return list(self.fo_minutes.get(code, []))[-count:]

    def get_option_chain(self, contracts, *, strikes) -> tuple[list[FoQuote], list[str]]:
        self._record("get_option_chain")
        wanted = set(strikes)
        quotes, errors = [], []
        for c in contracts:
            if c.strike is None or c.strike not in wanted:
                continue
            q = self.fo_quotes.get(c.code)
            (quotes.append(q) if q is not None else errors.append(c.code))
        return quotes, errors

    def get_kospi200(self) -> tuple[Decimal, datetime | None]:
        self._record("get_kospi200")
        if self.kospi200 is None:
            raise KisApiError("FAKE", "코스피200 없음")
        return self.kospi200


class FakeBroker:
    def __init__(
        self,
        account: BrokerAccount,
        *,
        positions: list[Position] | None = None,
        cash: dict[Market, Cash] | None = None,
        fail: set[str] | None = None,
        fills: dict[tuple[Market, date], list[Fill]] | None = None,
    ) -> None:
        self.account = account
        self.positions = positions or []
        self.cash = cash or {}
        self.fail = fail or set()
        self.fills = fills or {}
        self.calls: list[str] = []
        self.orders: list[OrderRequest] = []
        self.cancelled: list[str] = []

    def _record(self, name: str) -> None:
        self.calls.append(name)
        if name in self.fail:
            raise KisApiError("FAKE", f"가짜 실패 주입: {name}")

    def get_positions(self, market: Market) -> list[Position]:
        self._record("get_positions")
        return [p for p in self.positions if p.market == market]

    def get_balance(self, market: Market) -> tuple[list[Position], Cash]:
        self._record("get_balance")
        return self.get_positions(market), self.get_cash(market)

    def get_cash(self, market: Market) -> Cash:
        self._record("get_cash")
        if market not in self.cash:
            raise KisApiError("FAKE", f"현금 정보 없음: {market}")
        return self.cash[market]

    def get_fills(self, market: Market, d: date) -> list[Fill]:
        self._record("get_fills")
        return list(self.fills.get((market, d), []))

    def place_order(self, request: OrderRequest) -> OrderResult:
        self._record("place_order")
        self.orders.append(request)
        return OrderResult(
            order_no=f"FAKE-{len(self.orders)}", accepted=True, message="가짜 접수", org_no="00000"
        )

    def cancel_order(self, order_no: str, *, market: Market = "KR", **_: object) -> OrderResult:
        self._record("cancel_order")
        self.cancelled.append(order_no)
        return OrderResult(order_no=order_no, accepted=True, message="가짜 취소")
