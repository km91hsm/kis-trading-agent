"""scalp 테스트 공용 픽스처·헬퍼(Task 5~7 공유). `project` 는 루트 conftest.

계좌: `paper-main`(모의 auto KR·US) · `paper-two`(모의 auto KR) · `real-1`(실전 auto KR — 단타는
모의 전용이라 어떤 다리도 만들면 안 된다). 시각은 NOW(2026-11-04 수요일 10:30 KST)."""

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tests.features.accounts.conftest import make_account
from trader.features.accounts import api as accounts_api
from trader.features.regime import ports as regime_ports
from trader.features.scalp import engine
from trader.features.scalp.judge import ScalpJudge
from trader.features.scalp.settings import ScalpCfg
from trader.features.scan import ports as scan_ports
from trader.features.spot.ports import StockExecPortImpl
from trader.kernel.broker.fake import FakeBroker, FakeMarket
from trader.kernel.broker.models import Candle, Cash, Fill, Position
from trader.kernel.cli.boot import build_registry
from trader.kernel.config.loader import load
from trader.kernel.ledger import db
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations
from trader.kernel.session import run_store

NOW = datetime(2026, 11, 4, 1, 30, tzinfo=UTC)  # 10:30 KST (수요일)
D = date(2026, 11, 4)
NAV = Decimal("10000000")


def bars(
    n: int = 30,
    *,
    burst: bool = True,
    breakout: bool = True,
    start: str = "100000",
    price: str = "10000",
) -> list[Candle]:
    """합성 1분봉 n 개(start HHMMSS 부터 1분 간격). burst 면 마지막 5봉 거래량 4배, breakout 이면
    마지막 봉 종가가 +50(당일 고가·VWAP 동시 돌파) — 둘 다면 진입 신호가 난다."""
    h0, m0 = int(start[:2]), int(start[2:4])
    base = Decimal(price)
    out = []
    for i in range(n):
        h, m = divmod(h0 * 60 + m0 + i, 60)
        p = base + (Decimal(50) if breakout and i == n - 1 else Decimal(0))
        vol = 400 if burst and i >= n - 5 else 100
        out.append(
            Candle(date=D, open=p, high=p, low=p, close=p, volume=vol, time=f"{h:02d}{m:02d}00")
        )
    return out


class Notes:
    """보낸 알림을 기록하는 notifier. `ret` 로 send 반환값(False = 보내지 못함)을 정한다."""

    def __init__(self, ret: bool = True):
        self.msgs: list[str] = []
        self.ret = ret

    def send(self, text: str, *, escape: bool = True) -> bool:
        self.msgs.append(text)
        return self.ret


class SyncJudge(ScalpJudge):
    """테스트용: 제출 즉시 결과(스레드 없음). answer 는 값·예외 또는 (key, payload, schema) 함수."""

    def __init__(self, answer):
        self.answer = answer
        self.seen = []
        self._futs = {}
        self._at = {}

    def submit(self, key, payload, schema):
        self.seen.append((key, payload, schema))
        from concurrent.futures import Future

        f = Future()
        ans = self.answer(key, payload, schema) if callable(self.answer) else self.answer
        if isinstance(ans, BaseException):
            f.set_exception(ans)
        else:
            f.set_result(ans)
        self._futs[key] = f


@pytest.fixture
def settings(project):
    """settings.yaml 이 없으면 기본값 — 단 `scalp.enabled=True`."""
    s = load("settings", build_registry(discover()), path=project / "config" / "settings.yaml")
    return s.model_copy(update={"scalp": ScalpCfg(enabled=True)})


@pytest.fixture
def conn(project):
    c = db.connect()
    db.init_schema(c, all_migrations(discover()))
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def accounts():
    return [
        make_account("paper-main", "paper", "auto", ["KR", "US"]),
        make_account("paper-two", "paper", "auto", ["KR"]),
        make_account("real-1", "real", "auto", ["KR"]),
    ]


@pytest.fixture
def brokers(accounts):
    """계좌마다 FakeBroker(KR 예수금 1천만원, US 1만달러)."""
    out = {}
    for a in accounts:
        cash = {
            "KR": Cash(currency="KRW", available=NAV, total=NAV),
            "US": Cash(currency="USD", available=Decimal("10000"), total=Decimal("10000")),
        }
        out[a.id] = FakeBroker(a, cash=cash)
    return out


@pytest.fixture
def market():
    """가짜 시세(테스트가 `minutes`·`prices` 를 채운다)."""
    return FakeMarket(prices={}, minutes={}, now=lambda: NOW)


@pytest.fixture
def accounts_port(accounts, brokers, market):
    """`AccountsPort` 모양(계좌·브로커·시세 고정, 모드·일시정지는 accounts 기능 원장 조회)."""
    return SimpleNamespace(
        accounts=lambda: accounts,
        brokers=lambda: brokers,
        market_client=lambda: market,
        policy=accounts_api.effective_mode,
        paused=accounts_api.is_paused,
        holiday_fn=lambda: None,
    )


@pytest.fixture
def ports(settings, accounts_port):
    """`Boot.ports` 모양. stock_exec 는 spot 구현체가 같은 dict 를 지연 참조한다."""
    out = {
        "accounts": accounts_port,
        "regime": regime_ports.build_port(SimpleNamespace(settings=settings)),
        "scan": scan_ports.build_port(SimpleNamespace(settings=settings)),
    }
    out["stock_exec"] = StockExecPortImpl(settings, out)
    return out


@pytest.fixture
def scalp_ctx(ports, settings, conn, project):
    """`scalp_ctx(**kw) -> engine.ScalpContext` 공장(기본: KR 09:00~15:30, now=NOW, Notes)."""

    def make(**kw) -> engine.ScalpContext:
        base = {
            "settings": settings,
            "conn": conn,
            "ports": ports,
            "market_client": ports["accounts"].market_client(),
            "open_hm": "09:00",
            "close_hm": "15:30",
            "notifier": Notes(),
            "now": lambda: NOW,
            "state": project / "state",
        }
        return engine.ScalpContext(**(base | kw))

    return make


def scan_checkpoint(project, market: str, picks: list[str], targets: list[dict] = ()) -> None:
    """오늘(D) `_scan` 체크포인트: picks 종목 + analyze_targets."""
    run_store.write_checkpoint(
        run_store.market_dir(D, market, project / "state", create=True),
        "_scan",
        {"picks": [{"ticker": t} for t in picks], "analyze_targets": list(targets)},
    )


def fill_all(conn, brokers, market_client, *, now=NOW, price=None, d=D) -> None:
    """접수된 주문을 전부 체결시킨다(모의 브로커 체결 조회·보유에 반영 — 다음 틱의 동기화가
    읽는다). 가격은 지정가, 시장가는 price(없으면 현재가)."""
    for o in conn.execute("SELECT * FROM orders WHERE status IN ('accepted','partial')"):
        px = o["price"] or price or str(market_client.prices[o["ticker"]])
        mkt = o["market"]
        b = brokers[o["account_id"]]
        fills = b.fills.setdefault((mkt, d), [])
        if any(f.order_no == o["order_no"] for f in fills):
            continue
        fills.append(
            Fill(
                order_no=o["order_no"],
                ticker=o["ticker"],
                market=mkt,
                side=o["side"],
                qty=o["qty"],
                price=Decimal(str(px)),
                filled_at=now,
            )
        )
        held = {x.ticker: x.qty for x in b.positions}
        q = held.get(o["ticker"], 0) + (o["qty"] if o["side"] == "BUY" else -o["qty"])
        others = [x for x in b.positions if x.ticker != o["ticker"]]
        p = Decimal(str(px))
        b.positions = others + (
            [
                Position(
                    ticker=o["ticker"],
                    market=mkt,
                    qty=q,
                    avg_price=p,
                    current_price=p,
                    eval_amount=p * q,
                    pnl_pct=0.0,
                )
            ]
            if q > 0
            else []
        )
