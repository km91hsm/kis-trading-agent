"""해외지수 일별 관측: 공식 구조만 본뜬 합성 응답으로 시점 혼합을 막는다."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trader.kernel.broker.errors import KisApiError
from trader.kernel.broker.market import KisMarket

URL = "https://openapi.koreainvestment.com:9443/uapi/overseas-price/v1/quotations/inquire-daily-chartprice"
ASOF = datetime(2026, 11, 7, 1, tzinfo=UTC)  # 미국은 아직 11/6: 11/5까지만 허용


@pytest.mark.parametrize("symbol", ["SPX", "COMP"])
def test_us_index_pairs_output2_value_date_and_uses_bounded_us_market_window(
    real_client, respx_mock, symbol
):
    route = respx_mock.get(URL).respond(
        200,
        json={
            "rt_cd": "0",
            "output1": {"ovrs_nmix_prpr": "99999"},
            "output2": [
                {"stck_bsop_date": "20261104", "ovrs_nmix_prpr": "4100.25"},
                {"stck_bsop_date": "20261108", "ovrs_nmix_prpr": "8800"},
                {"stck_bsop_date": "20261106", "ovrs_nmix_prpr": "6600"},
                {"stck_bsop_date": "20261105", "ovrs_nmix_prpr": "4200.50"},
            ],
        },
    )
    got = KisMarket(real_client).get_index_us_daily(symbol, asof=ASOF)
    assert got == {"value": Decimal("4200.50"), "date": "2026-11-05", "symbol": symbol}
    assert len(route.calls) == 1
    req = route.calls[0].request
    assert dict(req.url.params) == {
        "FID_COND_MRKT_DIV_CODE": "N",
        "FID_INPUT_ISCD": symbol,
        "FID_INPUT_DATE_1": "20261023",
        "FID_INPUT_DATE_2": "20261105",
        "FID_PERIOD_DIV_CODE": "D",
    }
    assert req.headers["tr_id"] == "FHKST03030100"


@pytest.mark.parametrize(
    "raw_date,value",
    [
        ("20261106", "5000"),
        ("20261108", "5000"),
        ("20260230", "5000"),
        ("2026-11-05", "5000"),
        ("", "5000"),
        ("20261022", "5000"),
        ("20261105", "NaN"),
        ("20261105", "Infinity"),
        ("20261105", "0"),
        ("20261105", "-1"),
        ("20261105", "invalid"),
        ("20261105", ""),
    ],
)
def test_us_index_rejects_invalid_or_unavailable_daily_rows(
    real_client, respx_mock, raw_date, value
):
    respx_mock.get(URL).respond(
        200,
        json={
            "rt_cd": "0",
            "output1": {"ovrs_nmix_prpr": "99999"},
            "output2": [{"stck_bsop_date": raw_date, "ovrs_nmix_prpr": value}],
        },
    )
    assert KisMarket(real_client).get_index_us_daily("SPX", asof=ASOF) is None


def test_us_index_rejects_unapproved_symbol_before_request(real_client, respx_mock):
    with pytest.raises(ValueError):
        KisMarket(real_client).get_index_us_daily("NDX", asof=ASOF)
    assert not respx_mock.calls


def test_us_index_keeps_kis_business_failure(real_client, respx_mock):
    respx_mock.get(URL).respond(200, json={"rt_cd": "1", "msg_cd": "FAKE", "msg1": "합성 실패"})
    with pytest.raises(KisApiError):
        KisMarket(real_client).get_index_us_daily("COMP", asof=ASOF)
