"""spot 보유 부분 응답(`/accounts/{id}/positions`)·`/positions/overview`·`/positions/risk`.

옛 `tests/web/test_accounts_portfolio_orders.py` 의 손절선·계획 거리·드리프트·보유기간·전체 합산
4건(이름만 남아 있던 동작을 새 경로로 다시 씀)과 `test_portfolio_risk_requires_account_and_reports_
insufficient`, `test_fo_api.py::test_overview_excludes_fo` 이식 + 신규 `test_positions_merged_
across_lanes`."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tests.features.accounts.conftest import make_account
from tests.features.conftest import web_client as make_web_client
from tests.features.spot.helpers import ok_decision
from trader.features.accounts.ports import AccountsPortImpl
from trader.features.fo import store as fo_store
from trader.features.fo.feature import FEATURE as FO
from trader.features.scalp.feature import FEATURE as SCALP
from trader.features.spot.feature import FEATURE as SPOT
from trader.features.spot.ledger import ingest_checkpoint
from trader.kernel.config.secrets import Secrets
from trader.kernel.execution import ledger as xledger
from trader.kernel.ledger import db

NOW = datetime(2026, 11, 4, 6, 0, tzinfo=UTC)  # KST 15:00
D = "2026-11-04"
ROW_KEYS = {
    "account_id", "asset", "lane", "market", "ticker", "name", "qty", "avg_price", "last_price",
    "currency", "stop", "target", "at",
}  # fmt: skip


def test_position_links_keep_known_exchange_without_guessing(client, ledger):
    _nav(ledger, "paper-main", "1000", "100", market="US")
    _pos(ledger, "paper-main", "SYN", 1, "100", "110", "110", market="US")
    ledger.execute(
        "INSERT INTO ticker_exchanges(market,ticker,exchange,updated_at) VALUES (?,?,?,?)",
        ("US", "SYN", "NYSE", NOW.isoformat()),
    )
    ledger.commit()
    rows = client.get("/api/accounts/paper-main/positions", params={"market": "US"}).json()["items"]
    assert rows[0]["exchange"] == "NYSE"
    ledger.execute("DELETE FROM ticker_exchanges WHERE ticker=?", ("SYN",))
    ledger.commit()
    rows = client.get("/api/accounts/paper-main/positions", params={"market": "US"}).json()["items"]
    assert rows[0]["exchange"] is None


@pytest.fixture
def accounts():
    return [
        make_account("paper-main", "paper", "auto", markets=["KR", "US"]),
        make_account("paper-two", "paper", "auto"),
        make_account("real-main", "real", "off"),
        make_account("paper-fo", "paper", "auto", asset="fo", acnt_prdt_cd="03"),
    ]


class Ml:
    """종가표만 주는 ml 포트 흉내."""

    def __init__(self, table):
        self.table = table

    def close_table(self, market, since, until):
        return self.table


@pytest.fixture
def ml():
    return Ml({})


@pytest.fixture
def client(project, accounts, ml):
    regime = SimpleNamespace(hedge_tickers=lambda m: frozenset({"HEDGE"}) if m == "KR" else set())
    ports = {"accounts": AccountsPortImpl(accounts, Secrets()), "regime": regime, "ml": ml}
    return make_web_client(project, [SPOT, FO, SCALP], ports=ports, now=NOW)


@pytest.fixture
def ledger(project, client):
    c = db.connect(project / "state" / "trader.db")
    try:
        yield c
    finally:
        c.close()


def _nav(conn, acct, nav, cash, market="KR", d=D):
    conn.execute(
        "INSERT INTO account_nav(account_id, date, market, nav, cash, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (acct, d, market, nav, cash, "2026-11-04T06:00:00+00:00"),
    )
    conn.commit()


def _pos(conn, acct, ticker, qty, avg, cur, ev, market="KR", d=D, asset="stock"):
    conn.execute(
        "INSERT INTO positions_snapshot(account_id, date, market, asset, ticker, qty, avg_price, "
        "current_price, eval_amount, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (acct, d, market, asset, ticker, qty, avg, cur, ev, "2026-11-04T06:00:00+00:00"),
    )
    conn.commit()


def _decide(conn, ticker, d=date(2026, 11, 3), **over):
    ingest_checkpoint(
        conn, d, "KR", ticker, "30_decision", {"data": ok_decision(ticker=ticker, **over)}
    )


def _rows(client, acct, **params):
    r = client.get(f"/api/accounts/{acct}/positions", params=params)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["partial"] is False
    return {x["ticker"]: x for x in body["items"]}


def test_portfolio_effective_stop(client, ledger):
    """실제 적용 손절선: 결정 손절가(결정) · 보유 묶음 뒤 결정들의 바닥(추적) · 평단 기본(기본 −N%).
    헤지 ETF 는 모니터가 손절하지 않으므로 stop 이 없다."""
    _nav(ledger, "paper-main", "1000000", "100000")
    for t in ("AAA", "BBB", "CCC", "HEDGE"):
        _pos(ledger, "paper-main", t, 1, "70000", "72000", "72000")
    _decide(ledger, "BBB", stop_loss=65000.0, take_profit=80000.0)
    # CCC: 보유 시작(10/30 매수) 뒤 결정 손절 66000 → 오늘 HOLD 가 손절을 비웠다 → 바닥 66000
    xledger.insert_order(
        ledger, decision_id=None, account_id="paper-main", ticker="CCC", market="KR",
        side="BUY", qty=1, order_type="LIMIT", price=Decimal("70000"), status="accepted",
        order_no="1", message="", mode="auto", created_at="2026-10-30T01:00:00+00:00",
    )  # fmt: skip
    _decide(ledger, "CCC", d=date(2026, 10, 31), stop_loss=66000.0)
    _decide(ledger, "CCC", d=date(2026, 11, 3), action="HOLD", stop_loss=None, take_profit=None)
    rows = _rows(client, "paper-main", market="KR")
    assert rows["AAA"]["stop"] == {"level": "59500.00", "source": "기본 −15%"}
    assert rows["AAA"]["target"] is None
    assert rows["BBB"]["stop"] == {"level": "65000.0", "source": "결정"}
    assert rows["BBB"]["target"] == {"level": "80000.0", "source": "결정"}
    assert rows["CCC"]["stop"] == {"level": "66000.0", "source": "추적"}
    assert rows["HEDGE"]["stop"] is None
    row = rows["AAA"]
    assert ROW_KEYS <= set(row)
    assert row["asset"] == "stock" and row["lane"] == "spot" and row["currency"] == "KRW"
    assert row["qty"] == 1 and row["avg_price"] == "70000" and row["last_price"] is None
    # at = 스냅샷 관측 시각(UTC ISO, merge 정렬용), 날짜는 snapshot_date
    assert row["at"] == "2026-11-04T06:00:00+00:00" and row["snapshot_date"] == D


def test_portfolio_position_plan_distances(client, ledger):
    _nav(ledger, "paper-main", "1000000", "100000")
    _pos(ledger, "paper-main", "BBB", 1, "70000", "72000", "72000")
    _decide(ledger, "BBB", stop_loss=64800.0, take_profit=79200.0, limit_price=71280.0)
    plan = _rows(client, "paper-main")["BBB"]["plan"]
    assert plan["action"] == "BUY" and plan["take_profit"] == 79200.0
    assert plan["dist_take_profit_pct"] == pytest.approx(0.1)
    assert plan["dist_stop_pct"] == pytest.approx(-0.1)
    assert plan["dist_limit_pct"] == pytest.approx(-0.01)


def test_portfolio_position_drift(client, ledger):
    """비중 = 평가/NAV, 드리프트(%p) = 현재 − 목표(BUY·ADD·TRIM 만)."""
    _nav(ledger, "paper-main", "1000000", "100000")
    _pos(ledger, "paper-main", "BBB", 2, "70000", "75000", "150000")
    _pos(ledger, "paper-main", "HHH", 1, "70000", "75000", "75000")
    _decide(ledger, "BBB", target_weight=0.1)
    _decide(ledger, "HHH", action="HOLD", target_weight=0.1)
    rows = _rows(client, "paper-main")
    assert rows["BBB"]["weight"] == pytest.approx(0.15) and rows["BBB"]["drift_pp"] == 5.0
    assert rows["HHH"]["drift_pp"] is None


def test_portfolio_positions_carry_horizon_profile(client, ledger):
    _nav(ledger, "paper-main", "1000000", "100000")
    _pos(ledger, "paper-main", "BBB", 1, "70000", "72000", "72000")
    _pos(ledger, "paper-main", "AAA", 1, "70000", "72000", "72000")
    _decide(ledger, "BBB", horizon_days=60)
    rows = _rows(client, "paper-main")
    assert rows["BBB"]["horizon"] == "long" and rows["BBB"]["horizon_days"] == 60
    assert rows["AAA"]["horizon"] is None and rows["AAA"]["plan"] is None


def test_positions_merged_across_lanes(client, ledger):
    """같은 경로를 세 레인이 나눠 낸다: 현물 계좌엔 fo 행이 없고, 파생 계좌엔 fo 행만(손절 출처는
    '손절' 로 시작)."""
    _nav(ledger, "paper-main", "1000000", "100000")
    _pos(ledger, "paper-main", "BBB", 1, "70000", "72000", "72000")
    with fo_store.tx(ledger):
        fo_store.insert_position(
            ledger, account_id="paper-fo", code="105W11", kind="FUTURES", contract_side="LONG",
            direction="LONG", vehicle="FUTURES", status="held", qty_target=2, qty_held=2,
            stop_pt=Decimal("345"), opened_at="2026-11-04T00:30:00+00:00",
        )  # fmt: skip
    spot = client.get("/api/accounts/paper-main/positions").json()
    assert spot["partial"] is False
    assert {x["lane"] for x in spot["items"]} == {"spot"}
    fo = client.get("/api/accounts/paper-fo/positions").json()
    assert [x["lane"] for x in fo["items"]] == ["fo"]
    assert fo["items"][0]["asset"] == "fo"
    assert fo["items"][0]["stop"]["source"].startswith("손절")
    assert client.get("/api/accounts/nobody/positions").json() == {"items": [], "partial": False}


def test_portfolio_overview_merges_same_ticker(client, ledger):
    """같은 환경의 같은 종목은 합친다: 수량 합·수량 가중 평단·계좌 목록·가장 낮은 손절선."""
    _nav(ledger, "paper-main", "1000000", "0")
    _nav(ledger, "paper-two", "1000000", "0")
    _pos(ledger, "paper-main", "BBB", 1, "60000", "72000", "72000")
    _pos(ledger, "paper-two", "BBB", 3, "80000", "72000", "216000")
    body = client.get("/api/positions/overview", params={"market": "KR"}).json()
    block = body["markets"]["KR"]["envs"]["paper"]
    (row,) = block["positions"]
    assert row["qty"] == 4 and Decimal(row["avg_price"]) == Decimal("75000")
    assert sorted(row["accounts"]) == ["paper-main", "paper-two"]
    assert row["stop"] == {"level": "51000.00", "source": "기본 −15%"}  # 60000 평단 쪽
    assert block["nav"] == "2000000" and Decimal(block["pnl"]) == Decimal("-12000")


def test_portfolio_overview_separates_envs(client, ledger):
    """가짜 돈과 진짜 돈은 섞지 않는다 — 모의·실전 블록이 따로, 스냅샷 없는 계좌는 따로 센다."""
    _nav(ledger, "paper-main", "1000000", "0")
    _nav(ledger, "real-main", "500000", "0")
    _pos(ledger, "real-main", "BBB", 1, "70000", "72000", "72000")
    body = client.get("/api/positions/overview").json()
    envs = body["markets"]["KR"]["envs"]
    assert envs["paper"]["nav"] == "1000000" and envs["paper"]["positions"] == []
    assert envs["real"]["nav"] == "500000" and len(envs["real"]["positions"]) == 1
    assert body["accounts_missing_snapshot"]["KR"] == ["paper-two"]
    assert body["accounts_missing_snapshot"]["US"] == ["paper-main"]
    assert body["configured"] == 3 and body["pending_approvals"] == 0


def test_portfolio_overview_excludes_fo(client, ledger):
    fo_store.write_nav_snapshot(
        ledger, account_id="paper-fo", date=D, nav=Decimal("999999"), cash=Decimal(0),
        margin_total=Decimal(0), add_margin=Decimal(0),
    )  # fmt: skip
    _pos(ledger, "paper-fo", "105W11", 1, "350", "351", "351", asset="fo")
    ledger.commit()
    body = client.get("/api/positions/overview").json()
    assert "paper-fo" not in str(body) and "999999" not in str(body)


def test_portfolio_overview_market_filter_and_sectors(client, ledger):
    """market 필터 + 섹터(모르는 종목 '기타'·헤지 ETF '헤지'·현금 '현금', 합 = NAV)."""
    _nav(ledger, "paper-main", "1000000", "400000")
    _nav(ledger, "paper-main", "5000", "5000", market="US")
    _pos(ledger, "paper-main", "BBB", 1, "500000", "500000", "500000")
    _pos(ledger, "paper-main", "HEDGE", 1, "100000", "100000", "100000")
    body = client.get("/api/positions/overview", params={"market": "KR"}).json()
    assert list(body["markets"]) == ["KR"]
    sectors = {s["sector"]: s for s in body["markets"]["KR"]["envs"]["paper"]["sectors"]}
    assert sectors["기타"]["weight"] == pytest.approx(0.5)
    assert sectors["헤지"]["tickers"] == ["HEDGE"] and sectors["현금"]["weight"] == 0.4
    assert body["markets"]["KR"]["envs"]["paper"]["unmapped"] == ["BBB"]
    assert client.get("/api/positions/overview", params={"market": "XX"}).status_code == 400


def test_portfolio_risk_requires_account_and_reports_insufficient(client):
    assert client.get("/api/positions/risk").status_code in (400, 422)
    assert client.get("/api/positions/risk", params={"account_id": "paper-fo"}).status_code == 400
    r = client.get("/api/positions/risk", params={"account_id": "paper-main", "market": "KR"})
    assert r.status_code == 200 and r.json()["insufficient"] == "잔고 스냅샷 없음"


def test_portfolio_risk_card_from_close_table(client, ledger, ml):
    """보유 비중 × 최근 수익 → VaR·CVaR 95%·베타·상관·위험 기여(합 1). 종가는 ml 포트."""
    _nav(ledger, "paper-main", "1000000", "400000")
    _pos(ledger, "paper-main", "AAA", 1, "300000", "300000", "300000")
    _pos(ledger, "paper-main", "BBB", 1, "300000", "300000", "300000")
    start = date(2026, 8, 1)
    days = [start + timedelta(days=i) for i in range(80)]
    ml.table = {
        "AAA": {d: 100 * (1 + 0.01 * ((i % 5) - 2)) for i, d in enumerate(days)},
        "BBB": {d: 100 * (1 + 0.02 * ((i % 3) - 1)) for i, d in enumerate(days)},
    }
    r = client.get("/api/positions/risk", params={"account_id": "paper-main", "market": "KR"})
    card = r.json()
    assert r.status_code == 200 and "insufficient" not in card, card
    assert card["var95"] > 0 and card["cvar95"] >= card["var95"]
    assert card["n_obs"] == 60 and card["missing"] == [] and card["partial"] is False
    assert sum(c["contribution"] for c in card["contributions"]) == pytest.approx(1.0)
    assert card["corr"]["tickers"] == ["AAA", "BBB"] and card["account_id"] == "paper-main"


def test_risk_card_limits_and_usage(project, accounts, ml):
    """`/positions/risk` 에 설정 한도(`limits`)와 지금 사용량(`usage`)을 싣는다 — 종가가 부족한
    `insufficient` 분기에서도. 섹터는 `ports["scan"]`, 일일 손익은 직전 NAV 대비. 헤지 ETF(게이트가
    빼는 `excluded_tickers`)는 종목 수·최대 비중·섹터에서 빠지고 현금으로도 치지 않는다."""
    sectors = {"AAA": "반도체", "BBB": "반도체", "CCC": "금융"}
    scan = SimpleNamespace(sector_of=lambda m: sectors.get)
    regime = SimpleNamespace(hedge_tickers=lambda m: frozenset({"HEDGE"}) if m == "KR" else set())
    ports = {
        "accounts": AccountsPortImpl(accounts, Secrets()), "ml": ml, "scan": scan, "regime": regime,
    }  # fmt: skip
    client = make_web_client(project, [SPOT], ports=ports, now=NOW)
    c = db.connect(project / "state" / "trader.db")
    try:
        _nav(c, "paper-main", "1100000", "1100000", d="2026-11-03")
        _nav(c, "paper-main", "1000000", "100000")
        _pos(c, "paper-main", "AAA", 1, "300000", "300000", "300000")
        _pos(c, "paper-main", "BBB", 1, "150000", "150000", "150000")
        _pos(c, "paper-main", "CCC", 1, "100000", "100000", "100000")
        _pos(c, "paper-main", "HEDGE", 1, "350000", "350000", "350000")  # 제외 — 가장 커도
    finally:
        c.close()
    risk = client.app.state.deps.settings.risk
    want_limits = {
        "max_position_pct": risk.max_position_pct, "max_positions": risk.max_positions,
        "max_sector_pct": risk.max_sector_pct, "min_cash_pct": risk.min_cash_pct,
        "daily_loss_stop_pct": risk.daily_loss_stop_pct,
    }  # fmt: skip
    card = client.get("/api/positions/risk", params={"account_id": "paper-main"}).json()
    assert card["insufficient"].startswith("종가 부족")  # ml 종가표가 비었다
    assert card["limits"] == want_limits
    u = card["usage"]
    assert u["position_pct_max"] == pytest.approx(0.30) and u["positions"] == 3
    assert u["sector_pct_max"] == pytest.approx(0.45) and u["cash_pct"] == pytest.approx(0.10)
    assert u["daily_loss_pct"] == pytest.approx(1000000 / 1100000 - 1)
    # 스냅샷이 없어도 한도는 있고 사용량은 모름(None)
    empty = client.get("/api/positions/risk", params={"account_id": "paper-two"}).json()
    assert empty["insufficient"] == "잔고 스냅샷 없음" and empty["limits"] == want_limits
    assert set(empty["usage"]) == set(u) and all(v is None for v in empty["usage"].values())


def _usage_with_cash(conn, **cash):
    from datetime import date as _date
    from decimal import Decimal as _D

    from trader.features.spot.risk_view import usage
    from trader.kernel.execution import ledger as _xledger

    _xledger.snapshot_positions(
        conn, account_id="paper-main", d=_date(2026, 11, 4), market="KR",
        nav=_D("1000000"), cash=_D("1000000"), positions=[], **cash,
    )  # fmt: skip
    return usage(conn, account_id="paper-main", market="KR", sector_of=None)


def test_usage_cash_pct_uses_settlement_aware_cash(project, accounts):
    """결제 전 매수대금이 남은 예수금이 아니라 주문 가능 금액으로 현금 비중을 계산한다."""
    from decimal import Decimal as _D

    make_web_client(project, [SPOT], ports={}, now=NOW)  # 원장 생성
    c = db.connect(project / "state" / "trader.db")
    try:
        got = _usage_with_cash(c, cash_available=_D("800000"))
    finally:
        c.close()
    assert got["cash_pct"] == pytest.approx(0.8)


def test_usage_cash_pct_falls_back_to_cash_for_old_rows(project, accounts):
    make_web_client(project, [SPOT], ports={}, now=NOW)  # 원장 생성
    c = db.connect(project / "state" / "trader.db")
    try:
        got = _usage_with_cash(c)
    finally:
        c.close()
    assert got["cash_pct"] == pytest.approx(1.0)

