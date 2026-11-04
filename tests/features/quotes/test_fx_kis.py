"""KIS 환율 원천 연결: 런타임 조립 때 `fx.kis_fx` 를 시세 클라이언트로 채운다.

순서 `get_fx_usdkrw(quotes.fx_code)`(FHKST03030100) → `get_fx_from_detail()`(HHDFS76200200 t_rate)
→ FRED → 404. 조사 `docs/research/2026-11-05-kis-fx-rate-api.md`.
네트워크 없음(가짜 시세 클라이언트)."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from tests.features.conftest import web_client
from trader.features.accounts.feature import FEATURE as ACCOUNTS
from trader.features.quotes import fx
from trader.features.quotes import routes as quotes_mod
from trader.features.quotes.feature import FEATURE
from trader.features.quotes.settings import QuotesSettings

NOW = datetime(2026, 11, 5, 2, 0, tzinfo=UTC)


class FxMarket:
    def __init__(self, primary=None, detail=None):
        self.primary, self.detail, self.calls = primary, detail, []

    def _ret(self, v):
        if isinstance(v, Exception):
            raise v
        return v

    def get_fx_usdkrw(self, code="FX@KRW"):
        self.calls.append(("get_fx_usdkrw", code))
        return self._ret(self.primary)

    def get_fx_from_detail(self):
        self.calls.append(("get_fx_from_detail",))
        return self._ret(self.detail)


@pytest.fixture
def wire(monkeypatch):
    """공용 실전 키가 있는 것처럼 하고 `factory.build_market` 을 가짜로. macro 는 기본 없음."""
    creds = type("C", (), {"app_key": "synthetic-key", "app_secret": "synthetic-secret"})()
    monkeypatch.setattr(
        quotes_mod, "load_secrets", lambda: type("S", (), {"kis_creds": lambda self, env: creds})()
    )
    monkeypatch.setattr(fx, "kis_fx", None)
    monkeypatch.setattr(fx, "macro_usdkrw", lambda: None)

    def use(market):
        monkeypatch.setattr(quotes_mod.factory, "build_market", lambda c: market)
        return market

    return use


def _client(project, *features):
    c = web_client(project, [FEATURE, *features], now=NOW)
    c.app.state.deps.settings.quotes.realtime_ws = False  # 허브 없이
    return c


def test_fx_code_setting_default_and_validation():
    assert QuotesSettings().fx_code == "FX@KRW"
    assert QuotesSettings(fx_code="FX@KRWKFTC").fx_code == "FX@KRWKFTC"
    for bad in ("", "   "):
        with pytest.raises(ValidationError):
            QuotesSettings(fx_code=bad)


def test_quotes_fx_uses_kis_daily_with_fx_code(project, wire):
    m = wire(FxMarket(primary={"rate": Decimal("1385.50"), "as_of": "2026-11-04"}))
    c = _client(project)
    c.app.state.deps.settings.quotes.fx_code = "FX@KRWKFTC"
    r = c.get("/api/quotes/fx")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "pair": "USDKRW", "rate": "1385.50", "as_of": "2026-11-04", "source": "kis",
    }  # fmt: skip
    assert m.calls == [("get_fx_usdkrw", "FX@KRWKFTC")]


def test_quotes_fx_kis_daily_empty_falls_to_detail(project, wire):
    m = wire(FxMarket(primary=None, detail={"rate": Decimal("1386.2"), "as_of": "2026-11-05"}))
    body = _client(project).get("/api/quotes/fx").json()
    assert body["source"] == "kis" and body["rate"] == "1386.2"
    assert m.calls == [("get_fx_usdkrw", "FX@KRW"), ("get_fx_from_detail",)]


def test_quotes_fx_kis_raising_falls_to_macro_then_404(project, wire, monkeypatch):
    wire(FxMarket(primary=RuntimeError("x"), detail=RuntimeError("y")))
    monkeypatch.setattr(fx, "macro_usdkrw", lambda: {"rate": "1388.5", "as_of": "2026-11-04"})
    body = _client(project).get("/api/quotes/fx").json()
    assert body["source"] == "macro" and body["rate"] == "1388.5"


def test_quotes_fx_kis_raising_without_macro_is_404(project, wire):
    wire(FxMarket(primary=RuntimeError("x"), detail=RuntimeError("y")))
    assert _client(project).get("/api/quotes/fx").status_code == 404


def test_accounts_overview_wires_kis_fx_first(project, wire):
    """quotes 경로를 먼저 부르지 않아도 개요가 런타임을 만들어 KIS 환율을 쓴다(하루 캐시가
    FRED 값으로 먼저 굳지 않게)."""
    wire(FxMarket(primary={"rate": Decimal("1385.5"), "as_of": "2026-11-04"}))
    r = _client(project, ACCOUNTS).get("/api/accounts/overview")
    assert r.status_code == 200, r.text
    assert r.json()["fx"]["rate"] == "1385.5"
