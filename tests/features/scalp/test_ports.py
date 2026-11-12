"""scalp 포트: 다른 레인(spot 리밸런스)이 단타 보유분을 건드리지 않게 오늘 열린 종목을 준다."""

from datetime import date

from trader.features.scalp import ports
from trader.features.scalp.state import save_state

D = date(2026, 11, 4)


def test_open_tickers_reads_state_file(tmp_path):
    p = ports.build_port(None)
    assert p.open_tickers(tmp_path, "KR", D) == set()  # 파일 없음
    save_state(tmp_path, D, "KR", {"open": [{"ticker": "005930"}, {"ticker": "000660"}]})
    assert p.open_tickers(tmp_path, "KR", D) == {"005930", "000660"}
    assert p.open_tickers(tmp_path, "US", D) == set()
    assert ports.PORT.name == "scalp"


def test_open_legs_gives_per_account_quantities(tmp_path):
    """계좌·종목별 단타 수량: 체결 수량이 있으면 그것, 없으면 요청 수량. 파일이 없으면 빈 dict."""
    p = ports.build_port(None)
    assert p.open_legs(tmp_path, "KR", D) == {}
    save_state(
        tmp_path, D, "KR",
        {"open": [
            {"ticker": "005930", "legs": {"a": {"req": 10}, "b": {"req": 7}}, "filled": {"a": 6}},
            {"ticker": "000660", "legs": {"a": {"req": 3}}},
            {"ticker": "035420"},  # 옛 모양(다리 없음) — 수량을 모르면 빼지 않는다
        ]},
    )  # fmt: skip
    assert p.open_legs(tmp_path, "KR", D) == {
        ("a", "005930"): 6, ("b", "005930"): 7, ("a", "000660"): 3,
    }  # fmt: skip
