"""quotes 잡 `names_fill`: 이름 없는 보유·주문 종목을 시세 클라이언트 종목 기본정보로 채우고
(source='broker'), 설정 `quotes.instrument_names` 고정 이름을 source='config' 로 적재한다.

옛 `tests/test_daemon.py::test_names_fill_job_fills_unnamed_tickers`·
`tests/test_names.py::test_configured_instrument_names_fill_etfs`·`test_fill_missing_names_from_broker`
이식. 치환: `daemon._names_fill_body`→`jobs.names_fill_body`, `deps.market_client`→
`ports["accounts"].market_client()`, 설정 `instrument_names{시장: {코드: 이름}}`→
`quotes.instrument_names{"<시장>:<코드>": 이름}`, source `kis`→`broker`."""

import threading
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from tests.kernel.daemon.conftest import FakeAccountsPort
from trader.features.quotes import jobs
from trader.features.quotes.feature import FEATURE
from trader.features.quotes.settings import QuotesSettings
from trader.kernel.cli.boot import build_registry
from trader.kernel.config.loader import load
from trader.kernel.daemon.runner import DaemonDeps
from trader.kernel.execution import ledger as xledger
from trader.kernel.ledger import control, db
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations

NOW = datetime(2026, 10, 29, 1, 0, tzinfo=UTC)  # 10:00 KST


@pytest.fixture
def conn(project):
    c = db.connect()
    db.init_schema(c, all_migrations(discover()))
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def settings(project):
    return load("settings", build_registry(discover()), path=project / "config" / "settings.yaml")


class NameMarket:
    """`get_stock_name` 만 가진 가짜 시세 클라이언트(호출 기록)."""

    def __init__(self, names):
        self.names, self.asked = names, []

    def get_stock_name(self, market, ticker, exchange=None):
        self.asked.append((market, ticker, exchange))
        return self.names.get(ticker)


def _deps(settings, market) -> DaemonDeps:
    return DaemonDeps(
        settings=settings, schedule=None, conn_factory=db.connect, state=None,
        now=lambda: NOW, markets=["KR", "US"], gates={}, stop_event=threading.Event(),
        dry_run=True, ports={"accounts": FakeAccountsPort(market_client=market)},
    )  # fmt: skip


def _hold(conn, *tickers, market="KR"):
    """한 스냅샷(같은 계좌·날짜·시장은 통째로 바뀐다)에 종목들을 1주씩."""
    xledger.snapshot_positions(
        conn, account_id="paper-main", d=date(2026, 10, 29), market=market, nav=Decimal(1),
        cash=Decimal(1), positions=[{"ticker": t, "market": market, "qty": 1} for t in tickers],
    )  # fmt: skip


def _names(conn):
    return {
        (r["market"], r["ticker"]): (r["name"], r["source"])
        for r in conn.execute("SELECT market, ticker, name, source FROM ticker_names")
    }


def test_names_fill_job_fills_unnamed_tickers(conn, settings):
    """이름 없는 보유·주문 종목만 조회해 source='broker' 로 남기고, 못 찾은 종목은 그날 다시
    묻지 않는다. 잡 선언은 `names_fill.interval_s`(기본 3600초)."""
    spec = {j.id: j for j in FEATURE.jobs}["names_fill"]
    assert (spec.trigger, spec.schedule_key, spec.interval_s, spec.start_soon_s) == (
        "interval", "names_fill.interval_s", 3600, 120,
    )  # fmt: skip
    sched = load("schedule", build_registry(discover()), path=None)
    assert sched.names_fill.interval_s == 3600
    _hold(conn, "777777", "005930", "888888", "_CASH")  # 현금 행은 대상 아님
    conn.execute(
        "INSERT INTO ticker_names(market, ticker, name, source, updated_at) "
        "VALUES ('KR', '005930', '삼성전자', 'balance', 't')"
    )
    conn.execute(
        "INSERT INTO orders(account_id, ticker, market, side, qty, order_type, status, mode, "
        "created_at, asset) VALUES ('paper-main', 'ZZZZ', 'US', 'BUY', 1, 'LIMIT', 'accepted', "
        "'auto', 't', 'stock')"
    )
    conn.execute(
        "INSERT INTO ticker_exchanges(market, ticker, exchange, updated_at) "
        "VALUES ('US', 'ZZZZ', 'NYS', 't')"
    )
    conn.commit()
    market = NameMarket({"777777": "합성 종목", "ZZZZ": "Synthetic Corp"})
    out = jobs.names_fill_body(_deps(settings, market))
    assert out["filled"] == 2 and out["asked"] == 3
    assert sorted(market.asked) == [
        ("KR", "777777", None), ("KR", "888888", None), ("US", "ZZZZ", "NYS"),
    ]  # fmt: skip
    got = _names(conn)
    assert got[("KR", "777777")] == ("합성 종목", "broker")
    assert got[("US", "ZZZZ")] == ("Synthetic Corp", "broker")
    assert got[("KR", "005930")] == ("삼성전자", "balance")
    assert control.get_flag(conn, jobs.MISS_FLAG) == {"KR:888888": "2026-10-29"}
    # 같은 날 다시 돌면 못 찾은 종목은 묻지 않는다
    market.asked.clear()
    assert jobs.names_fill_body(_deps(settings, market))["asked"] == 0 and market.asked == []
    assert jobs.MISS_FLAG.split(":")[0] in FEATURE.control_namespaces


def test_names_fill_skips_without_market_client(conn, settings):
    _hold(conn, "777777")
    out = jobs.names_fill_body(_deps(settings, None))
    assert out["skipped"] == "시세 클라이언트 없음"
    assert ("KR", "777777") not in _names(conn)


def test_configured_instrument_names_fill_etfs(conn, settings):
    """헤지·현금·지수 대용 ETF 는 DART 캐시에 없다 — 설정 `quotes.instrument_names` 를 같은 잡이
    source='config' 로 적재한다(시세 클라이언트가 없어도). 설정이 관측보다 우선한다(T18-R3):
    잔고·브로커 이름을 덮고, 설정을 고치면 다음 실행에 반영되며, 같은 값이면 쓰기 0건.
    설정으로 채워진 종목은 브로커에 묻지 않는다."""
    assert QuotesSettings().instrument_names == {}
    with pytest.raises(ValidationError):
        QuotesSettings(instrument_names={"252670": "키에 시장 없음"})

    def with_names(names):
        cfg = settings.quotes.model_copy(update={"instrument_names": names})
        return settings.model_copy(update={"quotes": cfg})

    st = with_names({
        "KR:252670": "KODEX 200선물인버스2X", "US:SH": "ProShares Short S&P500",
        "KR:069500": "설정 이름", "KR:114800": "KODEX 인버스",
    })  # fmt: skip
    conn.execute(
        "INSERT INTO ticker_names(market, ticker, name, source, updated_at) VALUES "
        "('KR', '069500', '관측 이름', 'balance', 't'), ('KR', '114800', '잘린이름', 'broker', 't')"
    )
    conn.commit()
    _hold(conn, "252670")
    out = jobs.names_fill_body(_deps(st, None))
    assert out["config"] == 4 and "skipped" in out
    got = _names(conn)
    assert got[("KR", "252670")] == ("KODEX 200선물인버스2X", "config")
    assert got[("US", "SH")] == ("ProShares Short S&P500", "config")
    assert got[("KR", "069500")] == ("설정 이름", "config")  # (a) 관측(balance)을 덮는다
    assert got[("KR", "114800")] == ("KODEX 인버스", "config")  # (a) 브로커 이름도 덮는다
    # (c) 같은 값이면 쓰기 없음, 설정으로 채운 종목은 브로커에 묻지 않는다
    market = NameMarket({})
    out = jobs.names_fill_body(_deps(st, market))
    assert out["config"] == 0 and market.asked == []
    # (b) 설정 이름을 고치면 다음 실행에 반영된다
    fixed = with_names({"KR:252670": "KODEX 200선물인버스2X(수정)"})
    assert jobs.names_fill_body(_deps(fixed, None))["config"] == 1
    assert _names(conn)[("KR", "252670")] == ("KODEX 200선물인버스2X(수정)", "config")


def test_names_fill_does_not_record_miss_when_lookup_raises(conn, settings):
    """시세 조회가 예외로 끝난 종목은 그날 miss 로 적지 않는다 — 조회는 됐는데 이름이 없을
    때만 miss(M-7). 다음 실행에 다시 묻는다."""

    class Flaky(NameMarket):
        def get_stock_name(self, market, ticker, exchange=None):
            self.asked.append((market, ticker, exchange))
            if ticker == "777777":
                raise TimeoutError("일시 오류")
            return self.names.get(ticker)

    _hold(conn, "777777", "888888")
    market = Flaky({})
    out = jobs.names_fill_body(_deps(settings, market))
    assert out["asked"] == 2 and out["filled"] == 0
    assert control.get_flag(conn, jobs.MISS_FLAG) == {"KR:888888": "2026-10-29"}
    market.asked.clear()
    jobs.names_fill_body(_deps(settings, market))
    assert market.asked == [("KR", "777777", None)]


def test_configured_name_overrides_null_source_row(conn, settings):
    """source 가 NULL 인 옛 행도 설정 이름으로 승격한다(`IS NOT 'config'`, T18)."""
    conn.execute(
        "INSERT INTO ticker_names(market, ticker, name, source, updated_at) "
        "VALUES ('KR', '252670', '옛 이름', NULL, 't')"
    )
    conn.commit()
    cfg = settings.quotes.model_copy(update={"instrument_names": {"KR:252670": "옛 이름"}})
    st = settings.model_copy(update={"quotes": cfg})
    assert jobs.names_fill_body(_deps(st, None))["config"] == 1
    assert _names(conn)[("KR", "252670")] == ("옛 이름", "config")
