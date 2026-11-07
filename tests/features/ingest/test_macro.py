from decimal import Decimal

import httpx
import pytest

from tests.features.ingest.helpers import T0, load
from trader.features.ingest.macro import Ecos, Fred, collect_macro
from trader.features.ingest.settings import MacroCfg
from trader.kernel.config.secrets import Secrets

FRED = "https://api.stlouisfed.org/fred/series/observations"
ECOS = "https://ecos.bok.or.kr/api/StatisticSearch/"


class FakeIndexMarket:
    def __init__(self, fail=False):
        self.fail = fail

    def get_index_kr(self, code="0001"):
        if self.fail:
            raise RuntimeError("KIS down")
        from trader.kernel.broker.models import Quote

        return Quote(
            ticker=code, market="KR", price=Decimal("3250.12"), change_pct=0.0038, volume=1, asof=T0
        )


def test_fred_latest_skips_missing(respx_mock):
    route = respx_mock.get(url__startswith=FRED).mock(
        return_value=httpx.Response(200, json=load("macro_fred.json"))
    )
    v = Fred("FRED-KEY-SECRET", httpx.Client()).latest("DGS10")
    assert v == {"value": 4.12, "date": "2026-10-07"}
    p = route.calls[0].request.url.params
    assert p["series_id"] == "DGS10" and p["file_type"] == "json" and p["sort_order"] == "desc"


def test_ecos_latest(respx_mock):
    route = respx_mock.get(url__startswith=ECOS).mock(
        return_value=httpx.Response(200, json=load("macro_ecos.json"))
    )
    v = Ecos("ECOS-KEY-SECRET", httpx.Client(), now=lambda: T0).latest("722Y001", "0101000")
    assert v == {"value": 2.5, "date": "2026-10-08"}
    assert "/ECOS-KEY-SECRET/json/kr/" in str(route.calls[0].request.url)


def test_ecos_error_result_raises(respx_mock):
    respx_mock.get(url__startswith=ECOS).mock(
        return_value=httpx.Response(200, json={"RESULT": {"CODE": "INFO-200", "MESSAGE": "없음"}})
    )
    try:
        Ecos("ECOS-KEY-SECRET", httpx.Client(), now=lambda: T0).latest("722Y001", "0101000")
    except RuntimeError as e:
        assert "INFO-200" in str(e) and "ECOS-KEY-SECRET" not in str(e)
    else:
        raise AssertionError("RuntimeError 가 나야 한다")


def test_no_key_error_section(respx_mock):
    out = collect_macro(
        MacroCfg(),
        Secrets(),
        market_client=FakeIndexMarket(),
        asof=T0,
        http=httpx.Client(),
        now=lambda: T0,
    )
    assert out["kospi"]["data"]["value"] == 3250.12 and out["kospi"]["error"] is None
    for key in ("sp500", "usdkrw", "us10y", "vix", "base_rate_us", "dxy", "wti", "nasdaq"):
        assert out[key]["error"] == "no api key", key
    assert out["base_rate_kr"]["error"] == "no api key"


def test_partial_failure_keeps_others(respx_mock):
    respx_mock.get(url__startswith=FRED).mock(
        side_effect=lambda req: (
            httpx.Response(500, text="boom")
            if req.url.params["series_id"] == "VIXCLS"
            else httpx.Response(200, json=load("macro_fred.json"))
        )
    )
    respx_mock.get(url__startswith=ECOS).mock(
        return_value=httpx.Response(200, json=load("macro_ecos.json"))
    )
    secrets = Secrets(FRED_API_KEY="f", ECOS_API_KEY="e")
    out = collect_macro(
        MacroCfg(),
        secrets,
        market_client=FakeIndexMarket(fail=True),
        asof=T0,
        http=httpx.Client(),
        now=lambda: T0,
    )
    assert out["vix"]["data"] is None and out["vix"]["error"]
    assert out["us10y"]["data"]["value"] == 4.12
    assert out["base_rate_kr"]["data"]["value"] == 2.5
    assert out["kospi"]["error"].startswith("RuntimeError")
    assert all(s["received_at"] == T0.isoformat() for s in out.values())


def test_disabled_sources(respx_mock):
    cfg = MacroCfg(enabled=True, fred=False, ecos=False)
    out = collect_macro(
        cfg,
        Secrets(FRED_API_KEY="f", ECOS_API_KEY="e"),
        market_client=None,
        asof=T0,
        http=httpx.Client(),
        now=lambda: T0,
    )
    assert out["us10y"]["error"] == "disabled" and out["base_rate_kr"]["error"] == "disabled"
    assert out["kospi"]["error"] == "no market client"


def test_collect_macro_closes_self_made_client(monkeypatch):
    made: list[httpx.Client] = []
    real = httpx.Client

    class Spy(real):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            made.append(self)

    monkeypatch.setattr(httpx, "Client", Spy)
    collect_macro(MacroCfg(), Secrets(), market_client=None, asof=T0, now=lambda: T0)
    assert len(made) == 1 and made[0].is_closed


def test_fred_and_ecos_respect_asof(respx_mock):
    """asof 뒤에 발표된 관측값은 뺀다 (재수집 때 미래 VIX 가 서킷브레이커 입력에 들어가지 않게)."""
    from datetime import date

    respx_mock.get(url__startswith=FRED).mock(
        return_value=httpx.Response(200, json=load("macro_fred.json"))
    )
    v = Fred("FRED-KEY-SECRET", httpx.Client()).latest("DGS10", asof=date(2026, 10, 6))
    assert v == {"value": 4.10, "date": "2026-10-06"}
    route = respx_mock.get(url__startswith=ECOS).mock(
        return_value=httpx.Response(200, json=load("macro_ecos.json"))
    )
    Ecos("ECOS-KEY-SECRET", httpx.Client(), now=lambda: T0).latest(
        "722Y001", "0101000", asof=date(2026, 10, 1)
    )
    assert "/20261001/" in str(route.calls[0].request.url)


def test_collect_macro_passes_asof_to_sources(respx_mock):
    from datetime import UTC, datetime

    fred = respx_mock.get(url__startswith=FRED).mock(
        return_value=httpx.Response(200, json=load("macro_fred.json"))
    )
    out = collect_macro(
        MacroCfg(fred=True, ecos=False),
        Secrets(FRED_API_KEY="k"),
        market_client=None,
        asof=datetime(2026, 10, 6, 12, 0, tzinfo=UTC),
    )
    assert out["us10y"]["data"]["date"] == "2026-10-06"
    yield_request = next(
        c.request for c in fred.calls if c.request.url.params["series_id"] == "DGS10"
    )
    assert yield_request.url.params["observation_end"] == "2026-10-06"


class DailyIndexMarket(FakeIndexMarket):
    def __init__(self, outcome="ok"):
        super().__init__()
        self.outcome = outcome
        self.requests = []

    def get_index_us_daily(self, symbol, *, asof):
        self.requests.append((symbol, asof))
        if self.outcome == "failure":
            raise RuntimeError("합성 지수 조회 실패")
        if self.outcome == "missing":
            return None
        return {"value": Decimal("4200.50"), "date": "2026-10-08", "symbol": symbol}


def test_kis_indices_selected_without_duplicate_fred_requests(respx_mock):
    fred = respx_mock.get(url__startswith=FRED).respond(200, json=load("macro_fred.json"))
    market = DailyIndexMarket()
    out = collect_macro(
        MacroCfg(ecos=False),
        Secrets(FRED_API_KEY="synthetic"),
        market_client=market,
        asof=T0,
        http=httpx.Client(),
        now=lambda: T0,
    )
    assert market.requests == [("SPX", T0), ("COMP", T0)]
    assert {c.request.url.params["series_id"] for c in fred.calls} == {
        "DEXKOUS",
        "DGS10",
        "VIXCLS",
        "DFF",
        "DTWEXBGS",
        "DCOILWTICO",
    }
    for key, symbol, name in [("sp500", "SPX", "S&P 500"), ("nasdaq", "COMP", "NASDAQ Composite")]:
        section = out[key]
        assert section["data"]["value"] == Decimal("4200.50")
        assert section["source"] == "KIS" and section["series_id"] == symbol
        assert section["instrument"] == name and section["unit"] == "index points"
        assert section["observed_date"] == "2026-10-08" and section["observed_at"] is None
        assert section["basis"] == "daily_observation"
        assert section["timestamp_precision"] == "day" and section["live_verified"] is False
        assert section["received_at"] == T0.isoformat() and section["fallback_reason"] is None


@pytest.mark.parametrize(
    "outcome,reason",
    [
        ("failure", "합성 지수 조회 실패"),
        ("missing", "no valid daily observation"),
        ("no_method", "method unavailable"),
        ("no_client", "no market client"),
        ("disabled", "disabled"),
    ],
)
def test_kis_index_fallback_preserves_actual_fred_identity(respx_mock, outcome, reason):
    from datetime import UTC, datetime

    # UTC 11/7이지만 미국은 11/6이므로 fallback도 11/5까지만 허용한다.
    asof = datetime(2026, 11, 7, 1, tzinfo=UTC)
    fred = respx_mock.get(url__startswith=FRED).respond(
        200,
        json={
            "observations": [
                {"date": "2026-11-06", "value": "9000"},
                {"date": "2026-11-05", "value": "4300"},
            ]
        },
    )
    market = (
        None
        if outcome == "no_client"
        else (FakeIndexMarket() if outcome == "no_method" else DailyIndexMarket(outcome))
    )
    cfg = MacroCfg(kis_indices=outcome != "disabled", ecos=False)
    out = collect_macro(
        cfg,
        Secrets(FRED_API_KEY="synthetic"),
        market_client=market,
        asof=asof,
        http=httpx.Client(),
        now=lambda: T0,
    )
    for key, series in [("sp500", "SP500"), ("nasdaq", "NASDAQCOM")]:
        section = out[key]
        assert section["source"] == "FRED" and section["series_id"] == series
        assert section["data"] == {"value": 4300.0, "date": "2026-11-05"}
        assert reason in section["fallback_reason"]
    index_requests = [
        c.request for c in fred.calls if c.request.url.params["series_id"] in {"SP500", "NASDAQCOM"}
    ]
    assert len(index_requests) == 2
    assert all(r.url.params["observation_end"] == "2026-11-05" for r in index_requests)
    if outcome == "disabled":
        assert not market.requests


def test_kis_indices_work_without_fred_key_and_missing_retains_fallback_gap():
    for outcome in ("ok", "missing"):
        out = collect_macro(
            MacroCfg(ecos=False),
            Secrets(),
            market_client=DailyIndexMarket(outcome),
            asof=T0,
            http=httpx.Client(),
            now=lambda: T0,
        )
        if outcome == "ok":
            assert out["sp500"]["source"] == "KIS" and out["sp500"]["data"]
        else:
            assert out["sp500"]["data"] is None and out["sp500"]["error"] == "no api key"
            assert "no valid daily observation" in out["sp500"]["fallback_reason"]
