"""`trader regime show|backtest|killswitch`.

옛 `tests/test_cli_regime.py::test_regime_show_and_backtest` 이식. 치환: `cli._build_market`
monkeypatch → `regime.cli._market_client`, 앱 `trader.kernel.cli.main.app`."""

import json
from datetime import date, timedelta
from decimal import Decimal

from typer.testing import CliRunner

from trader.features.regime import cli, killswitch
from trader.kernel.broker.fake import FakeMarket
from trader.kernel.broker.models import Candle
from trader.kernel.cli.main import app
from trader.kernel.ledger import control, db


def test_regime_show_and_backtest(project, monkeypatch):
    r = CliRunner().invoke(app, ["regime", "show"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out["hedge"]["max_weight_pct"] == 0.2 and out["markets"][0]["state"] is None
    closes = [100.0] * 230 + [100.0 - i for i in range(1, 41)] + [60.0] * 30
    d0 = date(2025, 1, 1)
    candles = [
        Candle(date=d0 + timedelta(days=i), open=c, high=c, low=c, close=Decimal(str(c)), volume=1)
        for i, c in enumerate(closes)
    ]
    monkeypatch.setattr(cli, "_market_client", lambda b: FakeMarket(candles={"069500": candles}))
    r = CliRunner().invoke(app, ["regime", "backtest", "--market", "KR", "--days", "300"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out["proxy"] == "069500" and out["candles"] == 300 and out["transitions"] >= 1
    assert out["bear_spans"][0]["max_count"] >= 2
    r = CliRunner().invoke(app, ["regime", "backtest", "--market", "JP"])
    assert r.exit_code == 1


def test_killswitch_cli_on_off_show(project):
    run = CliRunner()
    r = run.invoke(app, ["regime", "killswitch", "show"])
    assert r.exit_code == 0 and "킬스위치: 꺼짐" in r.output
    r = run.invoke(app, ["regime", "killswitch", "on", "--reason", "점검 중 수동 차단"])
    assert r.exit_code == 0 and "켜짐 — 점검 중 수동 차단" in r.output
    # 메모가 MIN_NOTE 자 미만이면 거부(상태 불변)
    r = run.invoke(app, ["regime", "killswitch", "off", "--note", "짧음"])
    assert r.exit_code == 1 and f"{killswitch.MIN_NOTE}자 이상" in r.output
    conn = db.connect()
    try:
        assert killswitch.reason(conn) == "점검 중 수동 차단"
    finally:
        conn.close()
    r = run.invoke(app, ["regime", "killswitch", "off", "--note", "원인 확인: 모의 입출금 반영"])
    assert r.exit_code == 0 and "킬스위치: 꺼짐" in r.output
    conn = db.connect()
    try:
        st = control.get_flag(conn, killswitch.FLAG)
        assert st["on"] is False and st["released_note"] == "원인 확인: 모의 입출금 반영"
    finally:
        conn.close()
