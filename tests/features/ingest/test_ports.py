import json
from datetime import UTC, datetime

from tests.features.ingest.helpers import FakeMarket
from trader.features.ingest.api import SourcesConfig
from trader.features.ingest.ports import InputsPortImpl
from trader.kernel.config import Secrets
from trader.kernel.ledger import db
from trader.kernel.platform import paths


def test_collect_writes_00_inputs_and_returns_payload(project):
    cfg = SourcesConfig.model_validate(
        {
            "edgar": {"user_agent": "trader test@example.com"},
            "news": {"enabled": False},
            "macro": {"enabled": False},
            "community": {"enabled": False},
        }
    )
    port = InputsPortImpl(Secrets(), cfg)
    market = FakeMarket()
    out = port.collect(
        ticker="005930",
        market="KR",
        exchange=None,
        asof=datetime.now(UTC),
        force=False,
        conn_factory=db.connect,
        market_client=market,
    )
    files = list(paths.state_dir().rglob("00_inputs.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8")) == out
    assert out["technical"]["quote"]["data"]["price"] == "71500"
    assert "get_daily_kr" in market.calls
    # 있으면 읽기만(시세 재호출 없음)
    again = port.collect(
        ticker="005930",
        market="KR",
        exchange=None,
        asof=datetime.now(UTC),
        force=False,
        conn_factory=db.connect,
        market_client=market,
    )
    assert again == out and market.calls.count("get_daily_kr") == 1


def test_collect_injects_peer_fn_from_ml_port(project):
    from types import SimpleNamespace

    cfg = SourcesConfig.model_validate(
        {
            "edgar": {"user_agent": "trader test@example.com"},
            "news": {"enabled": False},
            "macro": {"enabled": False},
            "community": {"enabled": False},
        }
    )
    ports: dict = {}
    port = InputsPortImpl(Secrets(), cfg, ports)

    def run(ticker):
        return port.collect(
            ticker=ticker, market="KR", exchange=None, asof=datetime.now(UTC),
            force=True, conn_factory=db.connect, market_client=FakeMarket(),
        )  # fmt: skip

    out = run("005930")
    assert "no sector peers" in json.dumps(out["fundamental"]["peer"], ensure_ascii=False)

    calls = []

    def peer_medians(**kw):
        calls.append(kw)
        return {"per": 12.5}

    ports["ml"] = SimpleNamespace(peer_medians=peer_medians)  # 포트는 생성 뒤에 채워진다
    out = run("000660")
    assert set(calls[0]) == {"market", "ticker", "asof_date", "sectors"}
    assert calls[0]["ticker"] == "000660" and calls[0]["market"] == "KR"
    assert out["fundamental"]["peer"]["data"] == {"per": 12.5}


def test_build_port_resolves_ml_lazily_from_boot_ports(project):
    from types import SimpleNamespace

    from trader.features.ingest import ports as ingest_ports

    cfg = SourcesConfig.model_validate({"news": {"enabled": False}})
    boot_ports: dict = {}
    boot = SimpleNamespace(secrets=Secrets(), registry=None, ports=boot_ports)
    orig = ingest_ports.load_sources
    ingest_ports.load_sources = lambda registry=None: cfg
    try:
        port = ingest_ports.build_port(boot)
    finally:
        ingest_ports.load_sources = orig
    assert port._ports is boot_ports


def _news(conn, title, *, at, tickers="[]", novel=1, published=None):
    conn.execute(
        "INSERT INTO news_items(market, source, title, url, url_hash, published_at, received_at, "
        "tickers_json, novel) VALUES ('KR', 's', ?, ?, ?, ?, ?, ?, ?)",
        (title, f"u-{title}", f"h-{title}", published, at.isoformat(), tickers, novel),
    )
    conn.commit()


def test_recent_filters_by_tickers_and_since(project, conn):
    """`recent` 는 since 이후·asof 이전 수신 뉴스만(룩어헤드 없음), 종목·novel 필터, 최신순."""
    from datetime import timedelta

    now = datetime(2026, 10, 27, 1, 30, tzinfo=UTC)
    _news(conn, "old", at=now - timedelta(hours=3), tickers='["005930"]')
    _news(conn, "a", at=now - timedelta(minutes=50), tickers='["005930"]', novel=0)
    _news(conn, "b", at=now - timedelta(minutes=10), tickers='["000660", "005930"]')
    _news(conn, "macro", at=now - timedelta(minutes=5))
    _news(conn, "future", at=now + timedelta(minutes=5), tickers='["005930"]')
    port = InputsPortImpl(Secrets(), SourcesConfig())
    since = now - timedelta(hours=1)
    got = port.recent(conn, "KR", tickers=["005930"], since=since, asof=now)
    assert [r["title"] for r in got] == ["b", "a"]
    assert got[0]["tickers"] == ["000660", "005930"] and got[0]["novel"] is True
    assert {"id", "title", "published_at", "received_at", "source"} <= set(got[0])
    every = port.recent(conn, "KR", tickers=None, since=since, asof=now)
    assert [r["title"] for r in every] == ["macro", "b", "a"]
    assert [r["title"] for r in port.recent(conn, "KR", tickers=None, since=since, asof=now,
                                            novel_only=True)] == ["macro", "b"]  # fmt: skip
    assert len(port.recent(conn, "KR", tickers=None, since=since, asof=now, limit=1)) == 1
    untagged = port.recent(conn, "KR", tickers=None, since=since, asof=now, untagged_only=True)
    assert [r["title"] for r in untagged] == ["macro"]  # 종목 태그 없는 시장·거시 뉴스만
    assert port.recent(conn, "US", tickers=None, since=since, asof=now) == []
