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
