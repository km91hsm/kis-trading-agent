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
