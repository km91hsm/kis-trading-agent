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


def test_build_port_reads_custom_saveticker_news_and_calendar(project, monkeypatch, conn):
    from types import SimpleNamespace

    from trader.features.ingest import ports as ingest_ports
    from trader.features.ingest.saveticker_ingest import ingest_news
    from trader.features.saveticker.settings import SaveTickerCfg
    from trader.kernel.config.time import KST

    asof = datetime.now(UTC)
    root = project / "custom-feed"
    latest = root / "data" / "latest"
    latest.mkdir(parents=True)
    stamp = asof.isoformat()
    (latest / "community_top5.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "stream": "community_top5",
                "observed_at": stamp,
                "items": [{"tickers": ["AAPL"], "title": "Apple", "rank": 1}],
            }
        )
    )
    (latest / f"calendar_{asof:%Y-%m}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "stream": f"calendar_{asof:%Y-%m}",
                "observed_at": stamp,
                "items": [{"title": "경제 일정", "scheduled_at": stamp}],
            }
        )
    )
    day = root / "data" / asof.astimezone(KST).date().isoformat()
    day.mkdir()
    (day / "news.jsonl").write_text(
        json.dumps(
            {
                "stream": "news_all",
                "received_at": stamp,
                "revision": 1,
                "item": {
                    "id": "custom-news",
                    "title": "사용자 폴더 뉴스",
                    "body": "Apple 소식",
                    "tickers": ["AAPL"],
                    "published_at": stamp,
                },
            }
        )
    )
    conn = db.connect()
    try:
        assert ingest_news(conn, root, now=lambda: asof)["inserted"] == 1
    finally:
        conn.close()
    cfg = SourcesConfig.model_validate(
        {
            "dart": {"enabled": False},
            "edgar": {"enabled": False},
            "news": {"google_rss": False, "naver": False},
            "macro": {"enabled": False},
            "community": {"enabled": False},
        }
    )
    monkeypatch.setattr(ingest_ports, "load_sources", lambda **kw: cfg)
    # 실제 build_port가 SaveTicker 설정을 읽어 collect까지 전달하는 경로다.
    monkeypatch.setattr(
        "trader.features.saveticker.api.load_cfg", lambda **kw: SaveTickerCfg(dir=str(root))
    )
    port = ingest_ports.build_port(SimpleNamespace(secrets=Secrets(), registry=None, ports={}))
    out = port.collect(
        ticker="AAPL", market="US", exchange="NAS", asof=asof, force=True, conn_factory=db.connect
    )
    assert out["news_macro"]["news"]["data"]["stock"][0]["title"] == "사용자 폴더 뉴스"
    data = out["sentiment"]["saveticker"]["data"]
    assert data["mentions"]["community_top5"][0]["name"] == "Apple"
    assert out["news_macro"]["events"]["data"]["upcoming"][0]["title"] == "경제 일정"


def test_inputs_port_delivers_activity_with_own_connection(project):
    from types import SimpleNamespace

    cfg = SourcesConfig.model_validate(
        {
            "dart": {"enabled": False},
            "edgar": {"enabled": False},
            "news": {"enabled": False},
            "macro": {"enabled": False},
            "community": {"enabled": False},
        }
    )
    asof = datetime.now(UTC)

    def snapshot(conn, market, *, asof):
        # Reading on the producer's connection would fail sqlite's thread check.
        conn.execute("SELECT 1")
        return {
            "source": "kis",
            "market": market,
            "currency": "KRW",
            "received_at": asof.isoformat(),
            "source_asof": None,
            "basis": "unknown",
            "status": "partial",
            "stale": False,
            "coverage": {"complete": False},
            "rankings": {
                "turnover": [
                    {"ticker": "005930", "turnover": "123456", "growth": {"status": "unavailable"}}
                ],
                "gain": [],
                "loss": [],
            },
            "errors": {},
        }

    port = InputsPortImpl(Secrets(), cfg, {"market_activity": SimpleNamespace(snapshot=snapshot)})
    out = port.collect(
        ticker="005930", market="KR", exchange=None, asof=asof, force=True, conn_factory=db.connect
    )
    assert out["market_activity"]["source"] == "kis"
    assert out["market_activity"]["rankings"]["turnover"][0]["turnover"] == "123456"
    assert out["market_activity"]["source_asof"] is None
    assert any("partial" in gap for gap in out["data_gaps"])


def test_activity_connection_failure_remains_a_data_gap(project):
    cfg = SourcesConfig.model_validate(
        {
            "dart": {"enabled": False},
            "edgar": {"enabled": False},
            "news": {"enabled": False},
            "macro": {"enabled": False},
            "community": {"enabled": False},
        }
    )

    def unavailable():
        raise RuntimeError("store unavailable")

    port = InputsPortImpl(Secrets(), cfg)
    out = port.collect(
        ticker="005930",
        market="KR",
        exchange=None,
        asof=datetime.now(UTC),
        force=True,
        conn_factory=unavailable,
    )
    assert out["market_activity"]["status"] == "unavailable"
    assert any("store unavailable" in gap for gap in out["data_gaps"])


def test_ranto_blog_reaches_input_and_research_context(project, respx_mock):
    from trader.kernel.session.tooling import compact_context

    respx_mock.get("https://rss.blog.naver.com/ranto28.xml").respond(
        200,
        text=(
            "<rss><channel><item><title>시장 의견</title><description>공개 RSS 발췌</description>"
            "<pubDate>Fri, 06 Nov 2026 10:00:00 +0900</pubDate>"
            "<link>https://blog.naver.com/ranto28/1</link></item></channel></rss>"
        ),
    )
    cfg = SourcesConfig.model_validate(
        {
            "dart": {"enabled": False},
            "edgar": {"enabled": False},
            "news": {"enabled": False},
            "macro": {"enabled": False},
            "community": {"reddit": False, "clien": False},
            "channels": {"naver_blog": ["ranto28"]},
        }
    )
    out = InputsPortImpl(Secrets(), cfg).collect(
        ticker="005930",
        market="KR",
        exchange=None,
        asof=datetime.now(UTC),
        force=True,
        conn_factory=None,
    )
    coverage = out["source_coverage"]["naver_blog/ranto28"]
    assert coverage["status"] == "present" and coverage["configured"] is True
    assert coverage["source_asof"] == "2026-11-06T01:00:00+00:00"
    assert coverage["received_at"]
    compact = compact_context(out)
    assert compact["blog_posts"][0]["title"] == "시장 의견"
    assert compact["blog_posts"][0]["received_at"]


def test_public_research_context_preserves_asof_gaps_and_blog_failure(
    conn, monkeypatch, respx_mock
):
    from trader.features.ingest import macro as macro_sources

    asof = datetime(2026, 11, 7, 1, tzinfo=UTC)
    cfg = SourcesConfig.model_validate({"channels": {"naver_blog": ["ranto28"]}})
    monkeypatch.setattr(
        macro_sources,
        "collect_macro",
        lambda *a, **kw: {
            "kospi": {
                "data": {"value": 3000, "asof": "2026-11-07T05:00:00Z"},
                "received_at": "2026-11-07T01:00:00Z",
                "error": None,
            }
        },
    )
    respx_mock.get("https://rss.blog.naver.com/ranto28.xml").respond(500)
    out = InputsPortImpl(Secrets(), cfg).research_context(conn, "US", asof=asof, now=lambda: asof)
    assert out["macro"]["kospi"]["data"] is None
    assert out["macro"]["kospi"]["status"] == "unavailable"
    assert out["source_coverage"]["naver_blog/ranto28"]["configured"] is True
    assert out["source_coverage"]["naver_blog/ranto28"]["status"] == "missing"
    assert out["blog_posts"] == []
    assert any("ranto28" in gap for gap in out["data_gaps"])
