"""`trader scalp backtest|report`.

옛 `tests/test_cli_regime.py::test_scalp_report_and_backtest_cli` 이식. 치환: `cli._build_market`
monkeypatch → `scalp.cli._market_client`, 앱 `trader.kernel.cli.main.app`, schedule.yaml 복사는
없앰(없으면 기본값으로 읽힌다)."""

import json
from datetime import date
from decimal import Decimal

from typer.testing import CliRunner

from trader.features.scalp import cli
from trader.kernel.broker.fake import FakeMarket
from trader.kernel.broker.models import Candle
from trader.kernel.cli.main import app


def test_scalp_report_and_backtest_cli(project, monkeypatch):
    r = CliRunner().invoke(app, ["scalp", "report", "--market", "KR"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out["enabled"] is False and out["summary"]["trades"] == 0
    assert out["judge_enabled"] is False
    assert out["judge"]["counts"] == {"go": 0, "no_go": 0, "none": 0}
    bars = [
        Candle(
            date=date(2026, 10, 24),
            open=1,
            high=1,
            low=1,
            close=Decimal(1),
            volume=1,
            time=f"09{m:02d}00",
        )
        for m in range(30)
    ]
    monkeypatch.setattr(cli, "_market_client", lambda b: FakeMarket(minutes={"005930": bars}))
    r = CliRunner().invoke(app, ["scalp", "backtest", "--ticker", "005930"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out["bars"] == 30 and out["today"] is None and out["config"]["enabled"] is False
    assert out["ticker"] == "005930" and out["market"] == "KR" and out["report"]["days"] == []


def test_scalp_cli_rejects_unknown_market(project):
    r = CliRunner().invoke(app, ["scalp", "report", "--market", "JP"])
    assert r.exit_code == 1 and "KR 또는 US" in r.output


def test_scalp_report_live_uses_market_client(project, monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "_market_client", lambda b: seen.append(b) or FakeMarket())
    r = CliRunner().invoke(app, ["scalp", "report", "--market", "US", "--live"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["market"] == "US" and len(seen) == 1


class _ExchangeMarket(FakeMarket):
    """US 분봉 요청의 (종목, 거래소) 를 기록한다."""

    def __init__(self):
        super().__init__()
        self.asked: dict[str, str] = {}

    def get_minute_us(self, symbol, exchange, *, count=120):
        self.asked[symbol] = exchange
        return []


def test_scalp_report_live_us_exchange_lookup_order(project, conn, monkeypatch):
    """T8-R2: US 거래소 = 판정 사건의 값 → 원장 `ticker_exchanges` → `NAS`."""
    from datetime import UTC, datetime

    from trader.features.scalp.state import event, save_state

    d = date(2026, 11, 4)
    now = datetime(2026, 11, 4, 15, 0, tzinfo=UTC)
    save_state(None, d, "US", {"open": [], "closed": [], "done": [], "halted": None})
    base = {"go": False, "signal_price": "100", "signal_time": "100000"}
    event(None, d, "US", "judge", now=now, ticker="AAPL", exchange="NYS", **base)
    event(None, d, "US", "judge", now=now, ticker="MSFT", **base)
    event(None, d, "US", "judge", now=now, ticker="TSLA", **base)
    for t in ("AAPL", "MSFT"):  # AAPL 은 사건 값이 이겨야 한다
        conn.execute(
            "INSERT INTO ticker_exchanges(market, ticker, exchange, updated_at) "
            "VALUES ('US', ?, 'AMS', ?)",
            (t, now.isoformat()),
        )
    conn.commit()
    mc = _ExchangeMarket()
    monkeypatch.setattr(cli, "_market_client", lambda b: mc)
    monkeypatch.setattr(cli, "run_date_for", lambda m, n: d)
    r = CliRunner().invoke(app, ["scalp", "report", "--market", "US", "--live"])
    assert r.exit_code == 0, r.output
    assert mc.asked == {"AAPL": "NYS", "MSFT": "AMS", "TSLA": "NAS"}
    assert json.loads(r.output)["judge"]["counts"]["no_go"] == 3
