"""scan 웹 자원: `/news/labels`(촉매·테마 라벨)·`/news/themes`(최신 테마 열기)·
`/settings/watchlist`(조회 토큰 없음, 변경 토큰) + SSE 키 `theme`·`watchlist`.

옛 `test_control_sse.py::test_watchlist_api_token_and_validation`·
`test_sse_snapshot_includes_triggers_and_watchlist` 이식 + 신규 라벨 엔드포인트."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from tests.features.conftest import AUTH, web_client
from tests.kernel.web.conftest import make_deps
from trader.features.scan.feature import FEATURE
from trader.kernel.ledger import db
from trader.kernel.ledger.control import set_flag

NOW = datetime(2026, 10, 9, 6, 0, tzinfo=UTC)
HEAT = {
    "market": "KR",
    "date": "2026-10-09",
    "observed_at": "2026-10-09T05:30:00+00:00",
    "themes": [
        {"theme": "반도체", "rank": 1, "score": 3.0, "members": ["005930", "000660"]},
        {"theme": "합성테마", "rank": 2, "score": 2.0, "members": ["035420"]},
        {"theme": "셋째", "rank": 3, "score": 1.0, "members": ["005930"]},
        {"theme": "넷째", "rank": 4, "score": 0.5, "members": ["005930"]},
    ],
}


def _client(project):
    return web_client(project, [FEATURE], now=NOW)


def _conn(project):
    return db.connect(project / "state" / "trader.db")


def _news(conn, source, tickers, market="KR"):
    cur = conn.execute(
        "INSERT INTO news_items(market, source, query, title, url, url_hash, received_at, "
        "tickers_json, lang) VALUES (?, ?, 'q', '합성 제목', ?, ?, ?, ?, 'ko')",
        (market, source, f"https://example.test/{source}/{tickers}",
         f"h-{source}-{tickers}-{market}", NOW.isoformat(), json.dumps(tickers)),
    )  # fmt: skip
    conn.commit()
    return cur.lastrowid


def test_news_rows_keep_tickers_and_labels_endpoint(project):
    c = _client(project)
    conn = _conn(project)
    set_flag(conn, "theme_heat:KR", HEAT)
    a = _news(conn, "google_rss", ["005930"])
    b = _news(conn, "saveticker", ["035420"])  # 세이브티커는 촉매가 아니다
    u = _news(conn, "google_rss", ["AAPL"], market="US")
    conn.close()
    r = c.get("/api/news/labels", params={"ids": f"{a},{b},{u},999"})
    assert r.status_code == 200
    body = r.json()
    # 상위 top_n(기본 3) 테마만 라벨로
    assert body[str(a)] == {"catalyst": True, "themes": ["반도체", "셋째"]}
    assert body[str(b)] == {"catalyst": False, "themes": ["합성테마"]}
    assert body[str(u)] == {"catalyst": True, "themes": []}  # US 테마 기록 없음
    assert "999" not in body
    assert c.get("/api/news/labels", params={"ids": ""}).json() == {}
    assert c.get("/api/news/labels", params={"ids": "1,x"}).status_code == 400
    many = ",".join(str(i) for i in range(1, 502))
    assert c.get("/api/news/labels", params={"ids": many}).status_code == 400


def test_news_themes_latest_heat(project):
    c = _client(project)
    r = c.get("/api/news/themes?market=KR").json()
    assert r["themes"] == [] and r["date"] is None and r["top_n"] == 3
    conn = _conn(project)
    set_flag(conn, "theme_heat:KR", HEAT)
    conn.close()
    r = c.get("/api/news/themes?market=KR").json()
    assert [t["theme"] for t in r["themes"]] == ["반도체", "합성테마", "셋째", "넷째"]
    assert r["observed_at"] == HEAT["observed_at"] and r["date"] == "2026-10-09"
    assert len(c.get("/api/news/themes?market=KR&n=2").json()["themes"]) == 2
    assert c.get("/api/news/themes?market=JP").status_code == 400


def test_watchlist_api_token_and_validation(project):
    c = _client(project)
    assert c.get("/api/settings/watchlist").json() == {"tickers": []}
    body = {"tickers": ["005930", "aapl"]}
    assert c.patch("/api/settings/watchlist", json=body).status_code == 401
    r = c.patch("/api/settings/watchlist", json=body, headers=AUTH)
    assert r.status_code == 200 and r.json() == {"tickers": ["005930", "AAPL"]}
    bad = {"tickers": ["bad ticker!"]}
    assert c.patch("/api/settings/watchlist", json=bad, headers=AUTH).status_code == 400
    assert c.patch("/api/settings/watchlist", json={"x": 1}, headers=AUTH).status_code == 422
    assert c.get("/api/settings/watchlist").json() == {"tickers": ["005930", "AAPL"]}
    assert c.patch("/api/settings/watchlist", json={"tickers": []}, headers=AUTH
                   ).json() == {"tickers": []}  # fmt: skip
    conn = _conn(project)
    rows = conn.execute("SELECT channel, actor, key FROM control_log ORDER BY id").fetchall()
    conn.close()
    assert [(r["channel"], r["key"]) for r in rows] == [("web", "watchlist")] * 2
    assert all(r["actor"] and r["actor"] != "t" for r in rows)


def test_sse_snapshot_includes_theme_and_watchlist(project):
    _client(project)
    deps = make_deps(project, conn_factory=lambda: _conn(project))
    keys = {k.key: k for k in FEATURE.sse_keys}
    assert set(keys) == {"theme", "watchlist"} and not any(k.noisy for k in keys.values())
    w0, t0 = keys["watchlist"].probe(deps), keys["theme"].probe(deps)
    conn = _conn(project)
    set_flag(conn, "watchlist", ["AAPL"])
    conn.execute(
        "INSERT INTO theme_heat(market, date, observed_at, theme, rank, score, catalyst_ok, "
        "tickers_json, components_json) VALUES ('KR', '2026-10-09', 'x', '반도체', 1, 1.0, 1, "
        "'[]', '{}')"
    )
    conn.commit()
    conn.close()
    assert keys["watchlist"].probe(deps) != w0 and keys["theme"].probe(deps) != t0


def test_nav_item():
    assert [(n.screen, n.id, n.label, n.order) for n in FEATURE.nav] == [
        ("data", "themes", "테마·감시목록", 20)
    ]


def test_labels_rejects_unicode_digits(project):
    assert _client(project).get("/api/news/labels", params={"ids": "١٢"}).status_code == 400
