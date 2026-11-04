"""USD/KRW 환율 조회(합성 응답): `FHKST03030100`(X/`FX@KRW`) 와 현재가상세 `t_rate` 대안.

조사 노트 `docs/research/2026-11-05-kis-fx-rate-api.md` — 실기 응답 자릿수·빈 응답은 미확인이라
구조만 본뜬 합성 값을 쓴다."""

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from trader.kernel.broker import tr_ids
from trader.kernel.broker.market import KisMarket

REAL = "https://openapi.koreainvestment.com:9443"
FX_DAILY = "/uapi/overseas-price/v1/quotations/inquire-daily-chartprice"
OVS_PRICE_DETAIL = "/uapi/overseas-price/v1/quotations/price-detail"
NOW = datetime(2026, 11, 4, 16, 30, tzinfo=UTC)  # 2026-11-05 01:30 KST (UTC 날짜와 다름)


@pytest.fixture
def market(real_client):
    return KisMarket(real_client, now=lambda: NOW)


def _ok(output1, output2):
    return httpx.Response(200, json={"rt_cd": "0", "output1": output1, "output2": output2})


def test_fx_daily_tr_registered_same_on_paper():
    assert tr_ids.path("OVS_FX_DAILY") == FX_DAILY
    assert tr_ids.tr_id("OVS_FX_DAILY", "real") == "FHKST03030100"
    assert tr_ids.tr_id("OVS_FX_DAILY", "paper") == "FHKST03030100"


def test_fx_usdkrw_reads_output1_and_latest_date(market, respx_mock):
    route = respx_mock.get(REAL + FX_DAILY).mock(
        return_value=_ok(
            {"ovrs_nmix_prpr": "1385.50", "hts_kor_isnm": "원/달러"},
            [
                {"stck_bsop_date": "20261104", "ovrs_nmix_prpr": "1384.00"},
                {"stck_bsop_date": "20261103", "ovrs_nmix_prpr": "1380.00"},
            ],
        )
    )
    got = market.get_fx_usdkrw()
    assert got == {"rate": Decimal("1385.50"), "as_of": "2026-11-04", "code": "FX@KRW"}
    req = route.calls[0].request
    p = req.url.params
    assert p["FID_COND_MRKT_DIV_CODE"] == "X" and p["FID_INPUT_ISCD"] == "FX@KRW"
    assert p["FID_INPUT_DATE_1"] == "20261029" and p["FID_INPUT_DATE_2"] == "20261105"  # KST
    assert p["FID_PERIOD_DIV_CODE"] == "D"
    assert req.headers["tr_id"] == "FHKST03030100"


def test_fx_usdkrw_zero_output1_falls_back_to_output2(market, respx_mock):
    route = respx_mock.get(REAL + FX_DAILY).mock(
        return_value=_ok(
            {"ovrs_nmix_prpr": "0.00"},
            [{"stck_bsop_date": "20261104", "ovrs_nmix_prpr": "1384.00"}],
        )
    )
    got = market.get_fx_usdkrw("FX@KRWKFTC")
    assert got == {"rate": Decimal("1384.00"), "as_of": "2026-11-04", "code": "FX@KRWKFTC"}
    assert route.calls[0].request.url.params["FID_INPUT_ISCD"] == "FX@KRWKFTC"


def test_fx_usdkrw_output1_only_uses_today_kst(market, respx_mock):
    respx_mock.get(REAL + FX_DAILY).mock(return_value=_ok({"ovrs_nmix_prpr": "1385.5"}, []))
    assert market.get_fx_usdkrw()["as_of"] == "2026-11-05"


def test_fx_usdkrw_empty_response_is_none(market, respx_mock):
    respx_mock.get(REAL + FX_DAILY).mock(return_value=_ok({}, []))
    assert market.get_fx_usdkrw() is None


def test_fx_from_detail_reads_t_rate(market, respx_mock):
    route = respx_mock.get(REAL + OVS_PRICE_DETAIL).mock(
        return_value=httpx.Response(
            200, json={"rt_cd": "0", "output": {"t_rate": "1386.20", "p_rate": "1382.10"}}
        )
    )
    got = market.get_fx_from_detail()
    assert got == {"rate": Decimal("1386.20"), "as_of": "2026-11-05", "code": "HHDFS76200200"}
    req = route.calls[0].request
    assert req.url.params["EXCD"] == "NAS" and req.url.params["SYMB"] == "AAPL"
    assert req.headers["tr_id"] == "HHDFS76200200" and len(route.calls) == 1


def test_fx_from_detail_without_rate_is_none(market, respx_mock):
    respx_mock.get(REAL + OVS_PRICE_DETAIL).mock(
        return_value=httpx.Response(200, json={"rt_cd": "0", "output": {"t_rate": ""}})
    )
    assert market.get_fx_from_detail() is None
