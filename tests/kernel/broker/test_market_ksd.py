"""예탁원 권리 일정(`KisMarket.ksd_schedule`) — 파싱·연속조회(옛 tests/risk/test_blackout.py)."""

from datetime import date

import httpx

from trader.kernel.broker.market import KisMarket

D = date(2026, 10, 27)
REAL = "https://openapi.koreainvestment.com:9443"


def test_kis_ksd_schedule_parses_and_pages(real_client, respx_mock):
    route = respx_mock.get(REAL + "/uapi/domestic-stock/v1/ksdinfo/paidin-capin").mock(
        side_effect=[
            httpx.Response(
                200,
                headers={"tr_cont": "F"},
                json={
                    "rt_cd": "0",
                    "cts": "NEXT",
                    "output1": [{"record_date": "20261030", "sht_cd": "005930", "isin_name": "가"}],
                },
            ),
            httpx.Response(
                200,
                json={
                    "rt_cd": "0",
                    "output1": [
                        {"record_date": "20261105", "sht_cd": "000660", "isin_name": "나"},
                        {"record_date": "", "sht_cd": "x"},
                    ],
                },
            ),
        ]
    )
    m = KisMarket(real_client)
    got = m.ksd_schedule("paidin", D, date(2026, 11, 26))
    assert [g["ticker"] for g in got] == ["005930", "000660"]
    assert got[0] == {"ticker": "005930", "kind": "paidin", "date": "2026-10-30", "name": "가"}
    second = route.calls[1].request
    assert second.url.params["CTS"] == "NEXT" and second.headers["tr_cont"] == "N"
    assert route.calls[0].request.headers["tr_id"] == "HHKDB669100C0"
    assert route.calls[0].request.url.params["GB1"] == "1"
