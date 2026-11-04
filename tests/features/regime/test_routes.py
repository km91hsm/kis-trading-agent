"""regime 「시스템」 웹: `GET /system/regime?market=`(옛 `system.regime_status` +
`test_regime_endpoint_and_portfolio_sectors` 의 국면 부분 이식 — 섹터 부분은 spot 몫) — 헤지 설정
요약 + 시장별 국면·오늘 시장 판단(`view`)·적중률 + 킬스위치 상태. nav `system/regime`."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.features.conftest import web_client
from trader.features.regime import killswitch, regime
from trader.features.regime.feature import FEATURE
from trader.kernel.ledger import control
from trader.kernel.registry.types import NavItem

NOW = datetime(2026, 11, 4, 1, 0, tzinfo=UTC)  # 10:00 KST


@pytest.fixture
def client(project):
    return web_client(project, [FEATURE], now=NOW)


def test_regime_endpoint_states_view_and_killswitch(client, conn):
    r = client.get("/api/system/regime").json()
    assert r["enabled"] is True and r["markets"][0]["state"] is None
    assert r["instruments"]["KR"]["inverse"]["symbol"] == "114800"
    assert r["killswitch"] == {"on": False, "reason": None, "source": None, "accounts": None,
                               "at": None}  # fmt: skip
    hedge = client.app.state.deps.settings.hedge
    regime.update(
        conn, "KR", closes=[100.0] * 210 + [100.0 - i for i in range(1, 31)], cfg=hedge,
        today=NOW.date(),
    )  # fmt: skip
    control.set_flag(conn, "market_view:KR", {
        "date": "2026-11-04", "stance": "RISK_OFF", "hedge_weight": 0.1, "leveraged": False,
        "confidence": 0.8,
    })  # fmt: skip
    killswitch.engage(conn, reason="수동", source="cli")
    r = client.get("/api/system/regime", params={"market": "KR"}).json()
    (kr,) = r["markets"]
    assert kr["market"] == "KR" and kr["state"] == "bear" and kr["signals"]["ma_below"] is True
    assert kr["view"] == {"stance": "RISK_OFF", "hedge_weight": 0.1, "leveraged": False,
                          "confidence": 0.8}  # fmt: skip
    assert r["killswitch"]["on"] is True and r["killswitch"]["reason"] == "수동"
    assert client.get("/api/system/regime", params={"market": "XX"}).status_code == 400


def test_regime_nav_item():
    assert NavItem("system", "regime", "국면", 30) in FEATURE.nav
