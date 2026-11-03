"""현금 단기금리 ETF(Stock D-115, 기본 끔): 하락 국면에만 남는 현금(예비 20% 제외)을 사고, 국면이
풀리면 전량 매도. 운영일 1회, 분석 대상에서 빠진다.

옛 `tests/orchestrator/test_cash_park.py` 7 이식 — 치환만: `Settings(cash_park=…)`→
`settings.model_copy`(`CashParkCfg`), `executor.ExecContext`→`conftest.ectx`(포트·정책 주입),
`FakeBroker(acct)`→`to_broker_account`, `Settings.excluded_tickers`→`excluded.excluded_tickers`,
`control.set_account_mode`→`modes.set_account_mode`, `repo.open_orders_today`→`xledger`."""

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

from tests.features.spot.conftest import ectx, make_account
from trader.features.accounts import modes
from trader.features.accounts.settings import to_broker_account
from trader.features.spot import cash_park
from trader.features.spot.excluded import excluded_tickers
from trader.features.spot.settings import CashParkCfg
from trader.kernel.broker.fake import FakeBroker
from trader.kernel.broker.models import Cash, Position
from trader.kernel.execution import ledger as xledger

D = date(2026, 10, 27)
NOW = datetime(2026, 10, 27, 1, 0, tzinfo=UTC)  # D 장중(주문 시각이 운영일 안)
CASH = {"KR": Cash(currency="KRW", available=Decimal(10_000_000), total=Decimal(10_000_000))}


class Market:
    def get_price_kr(self, ticker):
        return SimpleNamespace(price=Decimal("10000"))


def _on(settings):
    return settings.model_copy(update={"cash_park": CashParkCfg(enabled=True)})


def _ctx(conn, settings, ports, positions=()):
    acct = make_account("paper-main", "paper", "auto")
    broker = FakeBroker(to_broker_account(acct), cash=CASH, positions=list(positions))
    ctx = ectx(ports, settings, conn, [acct], {"paper-main": broker})
    ctx.now = lambda: NOW
    return ctx


def test_off_by_default(conn, settings, ports):
    r = cash_park.run(
        _ctx(conn, settings, ports), market_client=Market(), today=D, regime="bear", dry_run=True
    )
    assert r == {"skipped": "꺼짐"}


def test_bear_parks_spare_cash_once(conn, project, settings, ports):
    on = _on(settings)
    r = cash_park.run(
        _ctx(conn, on, ports), market_client=Market(), today=D, regime="bear", dry_run=True
    )
    (o,) = r["orders"]
    v = o["results"][0]
    assert o["action"] == "BUY" and v["approved"] and v["qty"] == 800  # (1000만 − 200만)/1만
    # dry_run 은 기록하지 않는다 — 같은 날 실제 실행을 막지 않게
    real = cash_park.run(
        _ctx(conn, on, ports), market_client=Market(), today=D, regime="bear", dry_run=False
    )
    assert real["orders"][0]["results"][0]["order"]["status"] == "accepted"
    again = cash_park.run(
        _ctx(conn, on, ports), market_client=Market(), today=D, regime="bear", dry_run=False
    )
    assert again == {"skipped": "오늘 이미 실행"}
    port = ports["regime"]
    assert "357870" in excluded_tickers(on, port, "KR")
    assert "357870" not in excluded_tickers(settings, port, "KR")


def test_unparks_when_not_bear(conn, project, settings, ports):
    held = Position(
        ticker="357870",
        market="KR",
        qty=50,
        avg_price=Decimal("10000"),
        current_price=Decimal("10000"),
        eval_amount=Decimal("500000"),
        pnl_pct=0.0,
    )
    on = _on(settings)
    r = cash_park.run(
        _ctx(conn, on, ports, [held]),
        market_client=Market(),
        today=D,
        regime="neutral",
        dry_run=True,
    )
    (o,) = r["orders"]
    assert o["action"] == "SELL" and o["results"][0]["qty"] == 50
    # bear 도 아니고 보유도 없으면 아무 주문 없음
    r2 = cash_park.run(
        _ctx(conn, on, ports),
        market_client=Market(),
        today=date(2026, 10, 28),
        regime="bull",
        dry_run=True,
    )
    assert r2["orders"] == []


def test_approve_mode_accounts_skipped(conn, project, settings, ports):
    ctx = _ctx(conn, _on(settings), ports)
    modes.set_account_mode(conn, ctx.accounts[0], "approve")
    assert cash_park.run(ctx, market_client=Market(), today=D, regime="bear", dry_run=False) == {
        "skipped": "오늘 이미 실행"
    }


def test_one_account_failure_does_not_block_others(conn, project, settings, ports):
    good = make_account("paper-b", "paper", "auto")
    ctx = _ctx(conn, _on(settings), ports)
    ctx.accounts.append(good)
    ctx.brokers["paper-b"] = FakeBroker(to_broker_account(good), cash=CASH)
    ctx.brokers["paper-main"].fail = {"get_balance"}
    r = cash_park.run(ctx, market_client=Market(), today=D, regime="bear", dry_run=False)
    by = {o["account_id"]: o for o in r["orders"]}
    assert "error" in by["paper-main"] and by["paper-b"]["action"] == "BUY"


def test_same_day_regime_flip_cancels_open_park_buy_and_allows_reentry(
    conn, project, settings, ports
):
    """2-O17: 하루 1회 done 이 같은 날 국면 전환의 반대 주문을 막았다 — 국면(목표 행동)별 상태로,
    bear 를 벗어나면 미체결 매수를 취소하고, 다시 bear 가 되면 새 세대로 다시 산다."""
    ctx = _ctx(conn, _on(settings), ports)
    first = cash_park.run(ctx, market_client=Market(), today=D, regime="bear", dry_run=False)
    assert first["orders"][0]["results"][0]["order"]["status"] == "accepted"
    flip = cash_park.run(ctx, market_client=Market(), today=D, regime="neutral", dry_run=False)
    assert flip["orders"][0]["cancelled"] == 1  # 미체결 매수 취소, 보유 없음 → 매도 없음
    assert ctx.brokers["paper-main"].cancelled
    assert xledger.open_orders_today(conn, "paper-main", "357870", "BUY", D, market="KR") == []
    again = cash_park.run(ctx, market_client=Market(), today=D, regime="bear", dry_run=False)
    assert again["orders"][0]["results"][0]["order"]["status"] == "accepted"  # 새 세대 — 키 별개
    assert cash_park.run(ctx, market_client=Market(), today=D, regime="bear", dry_run=False) == {
        "skipped": "오늘 이미 실행"
    }


def test_cancel_failure_keeps_flip_in_progress(conn, project, settings, ports):
    """2-O17 보완: 반대 방향 미체결을 취소하지 못하면 진행 중으로 남겨 다음 틱에 다시 본다."""
    ctx = _ctx(conn, _on(settings), ports)
    cash_park.run(ctx, market_client=Market(), today=D, regime="bear", dry_run=False)
    ctx.brokers["paper-main"].fail = {"cancel_order"}
    flip = cash_park.run(ctx, market_client=Market(), today=D, regime="neutral", dry_run=False)
    assert flip["orders"][0]["cancel_failed"] == ["357870"]
    ctx.brokers["paper-main"].fail = set()
    retry = cash_park.run(ctx, market_client=Market(), today=D, regime="neutral", dry_run=False)
    assert retry["orders"][0]["cancelled"] == 1


# --- monitor 틱 연결(신규): 결과 키·실패 격리·꺼짐 ---------------------------------------


def _tick_deps(project, settings, enabled=True):
    from dataclasses import replace

    from tests.features.spot.test_jobs_hedge import _hedge_deps
    from trader.features.regime import jobs as regime_jobs
    from trader.features.regime import ports as regime_ports

    prices = {"114800": Decimal("5000"), "357870": Decimal("10000")}
    deps = _hedge_deps(
        project, settings, closes=[100] * 30 + [95, 90, 85, 80, 75, 70], prices=prices
    )
    s = deps.settings.model_copy(update={"cash_park": CashParkCfg(enabled=enabled)})
    deps = replace(
        deps,
        settings=s,
        ports={**deps.ports, "regime": regime_ports.build_port(SimpleNamespace(settings=s))},
    )
    regime_jobs.regime_update(deps, "KR")
    return deps


def test_monitor_body_parks_in_bear_and_reports_key(project, settings, conn, monkeypatch):
    from tests.features.spot.test_jobs_hedge import JOB
    from trader.features.spot import jobs as spot_jobs
    from trader.features.spot import monitor as monitor_mod
    from trader.kernel.ledger import control

    deps = _tick_deps(project, settings)
    monkeypatch.setattr(monitor_mod, "monitor_once", lambda ctx, **kw: {"checked": 0})
    out = spot_jobs.monitor_body(deps, JOB)
    assert out["cash_park"]["regime"] == "bear"
    assert any(o.get("action") == "BUY" for o in out["cash_park"]["orders"])
    assert control.get_flag(conn, cash_park.done_key(date(2026, 10, 24)))
    assert "cash_park" not in spot_jobs.monitor_body(deps, JOB)  # 오늘 이미 실행 → 싣지 않음


def test_monitor_body_cash_park_off_or_failure_does_not_block(
    project, settings, conn, monkeypatch
):
    from tests.features.spot.test_jobs_hedge import JOB
    from trader.features.spot import jobs as spot_jobs
    from trader.features.spot import monitor as monitor_mod

    monkeypatch.setattr(monitor_mod, "monitor_once", lambda ctx, **kw: {"checked": 3})
    off = spot_jobs.monitor_body(_tick_deps(project, settings, enabled=False), JOB)
    assert "cash_park" not in off

    def boom(d):
        raise RuntimeError("x")

    monkeypatch.setattr(cash_park, "cash_park_tick", boom)
    on = _tick_deps(project, settings)
    out = spot_jobs.monitor_body(on, JOB)
    assert out["checked"] == 3 and out["cash_park"]["error"].startswith("RuntimeError")
    assert not any("현금" in m for m in on.notifier.msgs)  # 알림 없음(옛 동일)
