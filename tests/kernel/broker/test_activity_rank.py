from decimal import Decimal

import httpx
import pytest

from trader.kernel.broker.market import KisMarket

REAL = "https://openapi.koreainvestment.com:9443"


def test_us_activity_uses_official_parameters_and_preserves_missing(real_client, respx_mock):
    route = respx_mock.get(REAL + "/uapi/overseas-stock/v1/ranking/trade-pbmn").mock(
        return_value=httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "output1": {},
                "output2": [
                    {
                        "symb": "AAPL",
                        "rank": "7",
                        "last": "212.30",
                        "rate": "-1.25",
                        "tvol": "1234",
                        "tamt": "123456.78",
                    },
                    {"symb": "MSFT", "rank": "8", "last": "NaN", "tamt": ""},
                ],
            },
        )
    )
    mc = KisMarket(real_client)
    assert hasattr(mc, "activity_rank"), "market activity adapter is required"
    result = mc.activity_rank("US", "turnover", exchange="NAS")
    a, b = result.rows
    assert (a.ticker, a.exchange, a.currency, a.source_rank, a.price, a.turnover) == (
        "AAPL",
        "NAS",
        "USD",
        7,
        Decimal("212.30"),
        Decimal("123456.78"),
    )
    assert a.turnover_unit == "unknown"
    assert a.turnover_basis == "source_reported"
    assert a.turnover_source_field == "tamt"
    assert a.change_pct == pytest.approx(-0.0125)
    assert b.price is None and b.turnover is None and b.volume is None
    q = route.calls[0].request.url.params
    assert (q["NDAY"], q["VOL_RANG"], q["EXCD"]) == ("0", "0", "NAS")
    assert route.calls[0].request.headers["tr_id"] == "HHDFS76320010"


@pytest.mark.parametrize("kind,gubn", [("gain", "1"), ("loss", "0")])
def test_us_direction_and_bounded_continuation(real_client, respx_mock, kind, gubn):
    route = respx_mock.get(REAL + "/uapi/overseas-stock/v1/ranking/updown-rate").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"rt_cd": "0", "keyb": "next", "output2": [{"symb": "AAA", "tamt": "10"}]},
                headers={"tr_cont": "M"},
            ),
            httpx.Response(
                200,
                json={"rt_cd": "0", "keyb": "more", "output2": [{"symb": "BBB", "tamt": "20"}]},
                headers={"tr_cont": "M"},
            ),
        ]
    )
    mc = KisMarket(real_client)
    assert hasattr(mc, "activity_rank"), "market activity adapter is required"
    result = mc.activity_rank("US", kind, exchange="NYS", max_pages=2)
    assert [r.ticker for r in result.rows] == ["AAA", "BBB"]
    assert result.pages == 2 and result.truncated
    assert route.calls[0].request.url.params["GUBN"] == gubn
    assert route.calls[1].request.url.params["KEYB"] == "next"
    assert route.calls[1].request.headers["tr_cont"] == "N"


def test_domestic_activity_requests_turnover_not_volume(real_client, respx_mock):
    route = respx_mock.get(REAL + "/uapi/domestic-stock/v1/quotations/volume-rank").mock(
        return_value=httpx.Response(
            200,
            json={
                "rt_cd": "0",
                "output": [
                    {
                        "mksc_shrn_iscd": "005930",
                        "stck_prpr": "70000",
                        "prdy_ctrt": "2",
                        "acml_tr_pbmn": "999999",
                        "acml_vol": "14",
                        "data_rank": "2",
                    }
                ],
            },
        )
    )
    mc = KisMarket(real_client)
    assert hasattr(mc, "activity_rank"), "market activity adapter is required"
    result = mc.activity_rank("KR", "turnover")
    assert result.rows[0].turnover == Decimal("999999")
    assert result.rows[0].currency == "KRW"
    assert route.calls[0].request.url.params["FID_BLNG_CLS_CODE"] == "3"


def test_next_page_failure_keeps_first_page(real_client, respx_mock):
    respx_mock.get(REAL + "/uapi/overseas-stock/v1/ranking/trade-pbmn").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"rt_cd": "0", "keyb": "next", "output2": [{"symb": "AAA", "tamt": "10"}]},
                headers={"tr_cont": "M"},
            ),
            httpx.Response(200, json={"rt_cd": "1", "msg_cd": "ERR", "msg1": "failed"}),
        ]
    )
    mc = KisMarket(real_client)
    assert hasattr(mc, "activity_rank"), "market activity adapter is required"
    result = mc.activity_rank("US", "turnover", exchange="NAS")
    assert [r.ticker for r in result.rows] == ["AAA"]
    assert result.error and result.truncated


def test_us_activity_does_not_invent_ticker_for_missing_symbol(real_client, respx_mock):
    respx_mock.get(REAL + "/uapi/overseas-stock/v1/ranking/trade-pbmn").mock(
        return_value=httpx.Response(200, json={"rt_cd": "0", "output2": [{"tamt": "10"}]})
    )
    result = KisMarket(real_client).activity_rank("US", "turnover", exchange="NAS")
    assert result.rows == []


@pytest.mark.parametrize("kind,direction", [("gain", "0"), ("loss", "1")])
def test_domestic_direction_preserves_actual_rate(real_client, respx_mock, kind, direction):
    route = respx_mock.get(REAL + "/uapi/domestic-stock/v1/ranking/fluctuation").mock(
        return_value=httpx.Response(
            200, json={"rt_cd": "0", "output": [{"stck_shrn_iscd": "005930", "prdy_ctrt": "-2.5"}]}
        )
    )
    result = KisMarket(real_client).activity_rank("KR", kind)
    assert result.rows[0].change_pct == -0.025
    assert route.calls[0].request.url.params["fid_rank_sort_cls_code"] == direction


def test_candidate_bound_stops_even_with_more_pages(real_client, respx_mock):
    route = respx_mock.get(REAL + "/uapi/overseas-stock/v1/ranking/trade-pbmn").mock(
        return_value=httpx.Response(
            200,
            json={"rt_cd": "0", "keyb": "more", "output2": [{"symb": "AAA"}, {"symb": "BBB"}]},
            headers={"tr_cont": "M"},
        )
    )
    result = KisMarket(real_client).activity_rank("US", "turnover", exchange="NAS", limit=1)
    assert [r.ticker for r in result.rows] == ["AAA"]
    assert result.truncated and len(route.calls) == 1


def test_limiter_busy_on_next_page_preserves_received_page(real_client, respx_mock, monkeypatch):
    from trader.kernel.broker.client import RateLimitBusy

    respx_mock.get(REAL + "/uapi/overseas-stock/v1/ranking/trade-pbmn").mock(
        return_value=httpx.Response(
            200,
            json={"rt_cd": "0", "keyb": "more", "output2": [{"symb": "AAA"}]},
            headers={"tr_cont": "M"},
        )
    )
    calls = []

    def acquire():
        calls.append(1)
        if len(calls) > 1:
            raise RateLimitBusy(1)

    monkeypatch.setattr(real_client.limiter, "acquire", acquire)
    result = KisMarket(real_client).activity_rank("US", "turnover", exchange="NAS")
    assert [r.ticker for r in result.rows] == ["AAA"]
    assert result.error == "RateLimitBusy:BUSY" and result.truncated
