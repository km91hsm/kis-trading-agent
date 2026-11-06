"""ingest health 영역: 보유 종목의 최근 critical 공시."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from trader.features.ingest import health
from trader.kernel.ops.types import OpsContext

NOW = datetime(2026, 10, 27, 3, 0, tzinfo=UTC)


class Accounts:
    def accounts(self):
        return [
            SimpleNamespace(id="paper-main", env="paper", asset="stock", markets=["KR", "US"]),
            SimpleNamespace(id="paper-fo", env="paper", asset="fo", markets=["KR"]),
        ]


def ctx(conn, project):
    settings = SimpleNamespace(markets=["KR", "US"])
    return OpsContext(conn, settings, project / "state", NOW, {"accounts": Accounts()}, [], [])


def position(conn, ticker, market="KR", qty=10):
    conn.execute(
        "INSERT INTO positions_snapshot(account_id, date, market, ticker, qty, created_at) "
        "VALUES ('paper-main','2026-10-26',?,?,?,?)",
        (market, ticker, qty, NOW.isoformat()),
    )


def news(conn, ticker, title, market="KR", severity="critical", received=NOW):
    conn.execute(
        "INSERT INTO news_items(market, source, title, url, url_hash, received_at, tickers_json, "
        "severity) VALUES (?,?,?,?,?,?,?,?)",
        (market, "dart", title, f"https://x/{title}", title, received.isoformat(),
         json.dumps([ticker]), severity),
    )  # fmt: skip


def test_area_key_is_feature_id_and_registered():
    from trader.features.ingest.feature import FEATURE

    assert health.AREA.key == "ingest" and FEATURE.health_areas == (health.AREA,)


def test_disclosure_area_critical_for_held(conn, project):
    assert health.check(ctx(conn, project)) == []
    position(conn, "005930")
    position(conn, "000660", qty=0)  # 수량 0 은 보유가 아니다
    position(conn, "AAPL", market="US")
    news(conn, "000660", "청산 종목 공시")
    news(conn, "035720", "미보유 종목 공시")
    news(conn, "005930", "오래된 공시", received=NOW - timedelta(days=4))
    news(conn, "005930", "일반 공시", severity="info")
    news(conn, "005930", "유상증자 결정")
    conn.commit()
    items = health.check(ctx(conn, project))
    assert [(i.key, i.level, i.count, i.link) for i in items] == [
        ("ingest.disclosure.KR", "critical", 1, "/data#news")
    ]
    assert "유상증자 결정" in items[0].text
