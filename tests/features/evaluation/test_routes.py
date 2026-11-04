"""evaluation 웹: `GET /accounts/{id}/tax-lots`(옛 `portfolio.lots_router`, 표시 전용·CSV 유지)와
`/analytics/*`(옛 `test_performance_api.py` 3건·`test_rl_api.py` 2건 이식 + 계좌 비교·보정·복기)·
SSE `market_views`·`shadow`·`memory`·nav."""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from tests.features.accounts.conftest import make_account
from tests.features.conftest import web_client as make_web_client
from tests.features.evaluation.helpers import seed_matured
from trader.features.accounts.ports import AccountsPortImpl
from trader.features.evaluation import analytics_view, performance
from trader.features.evaluation.feature import FEATURE
from trader.features.evaluation.rl.policy import FixedPolicy
from trader.features.ml import api as ml_api
from trader.kernel.config.secrets import Secrets
from trader.kernel.execution import ledger as kledger
from trader.kernel.registry.types import NavItem

T0 = datetime(2025, 1, 2, 15, 0, tzinfo=UTC)


@pytest.fixture
def client(project):
    accounts = [
        make_account("paper-main", "paper", "auto", markets=["KR", "US"]),
        make_account("paper-fo", "paper", "auto", asset="fo", acnt_prdt_cd="03"),
        make_account("real-main", "real", "approve"),
    ]
    return make_web_client(
        project, [FEATURE], ports={"accounts": AccountsPortImpl(accounts, Secrets())}
    )


def _trade(conn, side, qty, px, days):
    ts = (T0 + timedelta(days=days)).isoformat()
    oid = kledger.insert_order(
        conn, decision_id=None, account_id="paper-main", ticker="AAPL", market="US", side=side,
        qty=qty, order_type="LIMIT", price=Decimal(px), status="filled", order_no=None,
        message="", mode="auto", created_at=ts,
    )  # fmt: skip
    kledger.insert_fill(
        conn, order_id=oid, account_id="paper-main", market="US", ticker="AAPL", side=side,
        qty=qty, price=Decimal(px), filled_at=ts, order_no=None,
    )  # fmt: skip


def test_tax_lots_json_and_csv(client, conn):
    for side, qty, px, days in [("BUY", 2, "100", 0), ("SELL", 2, "90", 10), ("BUY", 1, "95", 20)]:
        _trade(conn, side, qty, px, days)
    r = client.get("/api/accounts/paper-main/tax-lots", params={"market": "US"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["realized_pnl"] == "-20" and body["summary"]["wash_sales"] == 1
    assert body["realized"][0]["wash_sale"] is True and len(body["open_lots"]) == 1
    assert client.get(
        "/api/accounts/paper-main/tax-lots", params={"market": "US", "year": 2024}
    ).json()["realized"] == []
    csv = client.get("/api/accounts/paper-main/tax-lots", params={"market": "US", "format": "csv"})
    assert csv.headers["content-type"].startswith("text/csv")
    assert 'filename="tax_lots_paper-main_US.csv"' in csv.headers["content-disposition"]
    head = csv.text.lstrip("﻿").splitlines()[0]
    assert head == "ticker,qty,buy_date,sell_date,cost,proceeds,pnl,term,wash_sale"


def test_tax_lots_requires_known_stock_account_and_market(client):
    assert client.get("/api/accounts/nobody/tax-lots").status_code == 404
    assert client.get("/api/accounts/paper-fo/tax-lots").status_code == 404
    assert client.get(
        "/api/accounts/paper-main/tax-lots", params={"market": "XX"}
    ).status_code == 400
    r = client.get("/api/accounts/paper-main/tax-lots")  # market 생략 = 계좌 첫 시장
    assert r.status_code == 200 and r.json()["market"] == "KR"


# --- /analytics -----------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_cache():
    """성과 리포트 캐시는 프로세스 전역이라 테스트마다 비운다."""
    analytics_view._CACHE.clear()
    yield
    analytics_view._CACHE.clear()


def test_cached_report_is_lru_bounded(monkeypatch):
    """리포트 캐시는 `CACHE_MAX` 개까지 — 가장 오래 안 쓴 키부터 버린다(M6)."""
    built = []
    monkeypatch.setattr(analytics_view, "CACHE_MAX", 3)
    monkeypatch.setattr(analytics_view, "_version", lambda *a, **kw: ("v",))

    def report(conn, state, account_id, market, *, days, asset):
        built.append(days)
        return {"days": days}

    monkeypatch.setattr(analytics_view.performance, "report", report)
    for days in (1, 2, 3):
        analytics_view.cached_report(None, None, "paper-main", "KR", days)
    analytics_view.cached_report(None, None, "paper-main", "KR", 1)  # 1 을 최근으로
    analytics_view.cached_report(None, None, "paper-main", "KR", 4)  # 2 가 밀려난다
    assert len(analytics_view._CACHE) == 3
    assert {k[2] for k in analytics_view._CACHE} == {1, 3, 4}
    assert built == [1, 2, 3, 4]


def _nav(conn, account_id, d, nav, market="KR"):
    kledger.snapshot_positions(
        conn, account_id=account_id, d=d, market=market, nav=Decimal(nav), cash=Decimal(1),
        positions=[],
    )  # fmt: skip


def test_performance_requires_account_and_market(client, conn):
    assert client.get("/api/analytics/performance").status_code == 400
    assert client.get("/api/analytics/performance?account_id=paper-main").status_code == 400
    assert client.get("/api/analytics/performance?account_id=ghost&market=KR").status_code == 400
    r = client.get("/api/analytics/performance?account_id=paper-main&market=KR")
    assert r.status_code == 200 and r.json()["metrics"]["days"] == 0
    _nav(conn, "paper-main", date(2026, 10, 8), 100)
    _nav(conn, "paper-main", date(2026, 10, 9), 103)
    r = client.get("/api/analytics/performance?account_id=paper-main&market=KR&days=30").json()
    assert abs(r["metrics"]["return"] - 0.03) < 1e-9 and r["nav"][-1]["value"] == "103"
    real = client.get("/api/analytics/performance?account_id=real-main&market=KR").json()
    assert real["metrics"]["days"] == 0
    bad = client.get("/api/analytics/performance?account_id=paper-main&market=KR&days=0")
    assert bad.status_code == 422
    us = client.get("/api/analytics/performance?account_id=real-main&market=US")
    assert us.status_code == 400  # 계좌가 쓰지 않는 시장


def test_performance_report_is_cached_until_inputs_change(client, conn, monkeypatch):
    """성과 리포트는 입력(스냅샷·체결·주문·피처)이 같으면 재계산하지 않는다(옛 리뷰 F22)."""
    n = {"calls": 0}
    orig = performance.report

    def counting(*a, **kw):
        n["calls"] += 1
        return orig(*a, **kw)

    monkeypatch.setattr(performance, "report", counting)
    url = "/api/analytics/performance?account_id=paper-main&market=KR"
    assert client.get(url).status_code == 200
    assert client.get(url).status_code == 200
    assert n["calls"] == 1
    conn.execute(
        "INSERT INTO positions_snapshot(account_id, date, ticker, market, qty, created_at) "
        "VALUES ('paper-main', '2026-10-09', '_CASH', 'KR', 0, 'x')"
    )
    conn.commit()
    assert client.get(url).status_code == 200
    assert n["calls"] == 2


def test_performance_shadow_pairs(client, conn):
    r = client.get("/api/analytics/shadow?market=KR").json()
    assert r["pairs"]["candidates_1n"]["n"] == 0 and r["recent"] == [] and r["min_pairs"] == 20
    for strat, ret in (("llm", 0.03), ("candidates_1n", 0.01), ("quant_topn", 0.02)):
        conn.execute(
            "INSERT INTO shadow_portfolios(market, date, strategy, weights_json, created_at, "
            "fwd_ret, matured_at, def_version) "
            "VALUES ('KR', '2026-10-01', ?, ?, 'x', ?, 'x', 2)",
            (strat, '{"A": 1.0}', ret),
        )
    conn.commit()
    r = client.get("/api/analytics/shadow?market=KR").json()
    assert r["pairs"]["candidates_1n"]["n"] == 1
    assert abs(r["pairs"]["candidates_1n"]["mean_diff"] - 0.02) < 1e-9
    assert r["pairs"]["candidates_1n"]["ci_low"] is None  # 쌍 1개는 구간 없음
    assert abs(r["pairs"]["quant_topn"]["mean_diff"] - 0.01) < 1e-9
    assert r["recent"][0] == {
        "date": "2026-10-01", "llm": 0.03, "candidates_1n": 0.01, "quant_topn": 0.02,
    }  # fmt: skip
    assert client.get("/api/analytics/shadow?market=US").json()["pairs"]["quant_topn"]["n"] == 0
    assert r["cash_effect"] == {"n": 0, "mean": None}


def test_performance_compare_lists_every_account(client, conn):
    """계좌·시장마다 한 행(NAV 이력 없는 계좌도 `metrics: null` 로 남는다). 레인은 asset fo → fo,
    그 외 spot. 계좌번호는 내보내지 않는다."""
    _nav(conn, "paper-main", date(2026, 10, 8), 100)
    _nav(conn, "paper-main", date(2026, 10, 9), 110)
    conn.execute(
        "INSERT INTO fo_nav_snapshot(account_id, date, nav, cash, margin_total, add_margin, "
        "created_at) VALUES ('paper-fo', '2026-10-08', '200', '1', '0', '0', 'x'), "
        "('paper-fo', '2026-10-09', '190', '1', '0', '0', 'x')"
    )
    conn.commit()
    body = client.get("/api/analytics/performance/compare?days=30").json()
    rows = {(r["account_id"], r["market"]): r for r in body["rows"]}
    assert set(rows) == {
        ("paper-main", "KR"), ("paper-main", "US"), ("paper-fo", "KR"), ("real-main", "KR"),
    }  # fmt: skip
    kr = rows[("paper-main", "KR")]
    assert kr["lane"] == "spot" and kr["env"] == "paper" and kr["currency"] == "KRW"
    assert abs(kr["metrics"]["return"] - 0.1) < 1e-9
    assert rows[("paper-main", "US")]["metrics"] is None
    assert rows[("paper-main", "US")]["currency"] == "USD"
    fo = rows[("paper-fo", "KR")]
    assert fo["lane"] == "fo" and abs(fo["metrics"]["return"] + 0.05) < 1e-9
    assert rows[("real-main", "KR")]["metrics"] is None
    assert body["days"] == 30 and "00000000" not in str(body)
    assert client.get("/api/analytics/performance/compare?days=0").status_code == 422


def test_calibration_recent_runs(client, conn):
    assert client.get("/api/analytics/calibration").json() == {"runs": []}
    for i, at in enumerate(("2026-10-01T00:00:00+00:00", "2026-10-02T00:00:00+00:00")):
        conn.execute(
            "INSERT INTO calibration_runs(created_at, n_decisions, active, json) "
            "VALUES (?, ?, ?, ?)",
            (at, 10 + i, i, '{"roles": {}}' if i else "{}"),
        )
    conn.commit()
    runs = client.get("/api/analytics/calibration?limit=1").json()["runs"]
    assert runs == [{
        "id": 2, "created_at": "2026-10-02T00:00:00+00:00", "n_decisions": 11, "active": True,
        "report": {"roles": {}},
    }]  # fmt: skip
    assert client.get("/api/analytics/calibration?limit=0").status_code == 422


def _warm(client):
    deps = client.app.state.deps
    s = deps.settings
    s = s.model_copy(update={"memory": s.memory.model_copy(update={"warmup": 0})})
    client.app.state.deps = dataclasses.replace(deps, settings=s)


def test_memory_recent_and_retrieve(client, conn):
    assert client.get("/api/analytics/memory").json()["entries"] == []
    seed_matured(conn, 3)
    recent = client.get("/api/analytics/memory?limit=2").json()
    assert recent["query"] is None and len(recent["entries"]) == 2
    assert recent["entries"][0]["decided_at"] >= recent["entries"][1]["decided_at"]
    assert "situation_vector" not in recent["entries"][0]
    # q = 종목: 그 종목의 최근 엔트리 상황으로 검색(워밍업 전이면 빈 목록 + warmup)
    r = client.get("/api/analytics/memory?q=T001").json()
    assert r["warmup"] is True and r["entries"] == [] and r["query"]["ticker"] == "T001"
    _warm(client)
    r = client.get("/api/analytics/memory?q=t001&limit=2").json()
    assert r["warmup"] is False and len(r["entries"]) == 2 and "score" in r["entries"][0]
    none = client.get("/api/analytics/memory?q=ZZZ").json()
    assert none["query"] is None and none["entries"] == []
    for bad in ("0", "201"):
        assert client.get(f"/api/analytics/memory?limit={bad}").status_code == 422


def test_rl_experiments_and_shadow_endpoints(client):
    assert client.get("/api/analytics/rl/experiments").json() == {"experiments": [], "active": None}
    v = ml_api.registry_save(
        "rl_sizer",
        FixedPolicy(0.1),
        {"summary": {}, "backend": "cem", "source": "features", "steps": 10, "seeds": 3},
    )
    assert client.get("/api/analytics/rl/experiments").json()["experiments"][0]["version"] == v
    assert client.get(f"/api/analytics/rl/experiments/{v}").status_code == 200
    assert client.get("/api/analytics/rl/experiments/bad").status_code == 400
    assert client.get("/api/analytics/rl/experiments/19990101_000000").status_code == 404
    assert client.get("/api/analytics/rl/shadow").status_code == 400
    assert client.get("/api/analytics/rl/shadow?account_id=nope").status_code == 400
    assert client.get("/api/analytics/rl/shadow?account_id=paper-main").json() == {"rows": []}
    rep = client.get("/api/analytics/rl/report?account_id=paper-main").json()
    assert rep["shadow"]["n"] == 0


def test_rl_report_rejects_bad_market(client):
    url = "/api/analytics/rl/report?account_id=paper-main"
    assert client.get(url + "&market=XX").status_code == 400
    assert client.get(url + "&market=KR").status_code == 200


def test_sse_keys_follow_old_fingerprints(client, conn):
    keys = {k.key: k for k in FEATURE.sse_keys}
    assert set(keys) == {"market_views", "shadow", "memory"}
    assert not any(k.noisy for k in keys.values())
    deps = client.app.state.deps
    before = {k: v.probe(deps) for k, v in keys.items()}
    assert before == {"market_views": "0::0", "shadow": "0:0", "memory": "0:0"}
    conn.execute(
        "INSERT INTO shadow_portfolios(market, date, strategy, weights_json, created_at, "
        "def_version) VALUES ('KR', '2026-10-01', 'llm', '{}', 'x', 2)"
    )
    conn.commit()
    seed_matured(conn, 1)
    after = {k: v.probe(deps) for k, v in keys.items()}
    assert after["shadow"] == "1:0" and after["memory"] == "1:1"
    assert after["market_views"] == before["market_views"]


def test_evaluation_nav_items():
    assert NavItem("performance", "performance-overview", "성과", 0) in FEATURE.nav
    assert NavItem("performance", "memory", "복기", 20) in FEATURE.nav
