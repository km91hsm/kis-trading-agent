"""scalp 웹: `/accounts/{id}/positions` 단타 조각(상태 파일 다리 → 행, 손절·목표 = 청산 규칙)과
SSE `scalp`(상태 파일 mtime + 보유 수)·nav."""

from __future__ import annotations

import pytest

from tests.features.accounts.conftest import make_account
from tests.features.conftest import web_client as make_web_client
from tests.features.scalp.conftest import NOW, D
from trader.features.accounts.ports import AccountsPortImpl
from trader.features.scalp.feature import FEATURE
from trader.features.scalp.state import save_state
from trader.kernel.config.secrets import Secrets
from trader.kernel.registry.types import NavItem


@pytest.fixture
def client(project):
    accounts = [
        make_account("paper-main", "paper", "auto", markets=["KR", "US"]),
        make_account("paper-two", "paper", "auto"),
        make_account("real-1", "real", "auto"),
    ]
    ports = {"accounts": AccountsPortImpl(accounts, Secrets())}
    return make_web_client(project, [FEATURE], ports=ports, now=NOW)


def _pos(ticker, legs, **over):
    return {
        "ticker": ticker, "entry_price": "10000", "entry_fill": "10010",
        "entry_at": "2026-11-04T01:10:00+00:00", "entry_time": "101000", "status": "held",
        "legs": {a: {"req": q, "buy": [], "sell": []} for a, q in legs.items()},
        "filled": dict(legs), **over,
    }  # fmt: skip


def _items(client, acct):
    body = client.get(f"/api/accounts/{acct}/positions").json()
    assert body["partial"] is False
    return {x["ticker"]: x for x in body["items"]}


def test_scalp_rows_use_exit_rules(project, client):
    """손절·목표는 청산 규칙(`stop_pct`·`take_profit_pct`, 판정자가 포지션별로 덮은 값 우선)을
    체결 평단에 적용한 값. 출처는 '단타 규칙'. 다리가 없는 계좌엔 행이 없다."""
    st = project / "state"
    save_state(st, D, "KR", {"open": [
        _pos("005930", {"paper-main": 3, "real-1": 1}),  # 실전 다리는 보이지 않는다
        _pos("000660", {"paper-main": 1, "paper-two": 2}, stop_pct=0.02, entry_fill=None),
    ]})  # fmt: skip
    rows = _items(client, "paper-main")
    a = rows["005930"]
    assert a["lane"] == "scalp" and a["asset"] == "stock" and a["market"] == "KR"
    assert a["qty"] == 3 and a["avg_price"] == "10010" and a["currency"] == "KRW"
    assert a["stop"] == {"level": "9909.90", "source": "단타 규칙"}
    assert a["target"] == {"level": "10160.150", "source": "단타 규칙"}
    assert a["at"] == "2026-11-04T01:10:00+00:00" and a["last_price"] is None
    b = rows["000660"]
    assert b["qty"] == 1 and b["avg_price"] == "10000"  # 체결 평단이 없으면 신호가
    assert b["stop"] == {"level": "9800.00", "source": "단타 규칙"}
    assert _items(client, "paper-two")["000660"]["qty"] == 2
    assert _items(client, "real-1") == {}
    assert _items(client, "nobody") == {}


def test_scalp_sse_key_changes_with_state(project, client):
    (key,) = [k for k in FEATURE.sse_keys if k.key == "scalp"]
    deps = client.app.state.deps
    before = key.probe(deps)
    save_state(project / "state", D, "KR", {"open": [_pos("005930", {"paper-main": 3})]})
    after = key.probe(deps)
    assert before != after and after["KR"].endswith("|1")


def test_scalp_nav_item():
    assert NavItem("today", "scalp-lane", "단타", 20) in FEATURE.nav

