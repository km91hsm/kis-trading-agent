"""evaluation 웹: `GET /accounts/{id}/tax-lots`(옛 `portfolio.lots_router`, 표시 전용·CSV 유지)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tests.features.accounts.conftest import make_account
from tests.features.conftest import web_client as make_web_client
from trader.features.accounts.ports import AccountsPortImpl
from trader.features.evaluation.feature import FEATURE
from trader.kernel.config.secrets import Secrets
from trader.kernel.execution import ledger as kledger

T0 = datetime(2025, 1, 2, 15, 0, tzinfo=UTC)


@pytest.fixture
def client(project):
    accounts = [
        make_account("paper-main", "paper", "auto", markets=["KR", "US"]),
        make_account("paper-fo", "paper", "auto", asset="fo", acnt_prdt_cd="03"),
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
