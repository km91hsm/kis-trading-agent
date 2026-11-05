"""파생 모니터 엔진(옛 `tests/fo/test_engine.py` 18건 이식). 치환: `ctx.notifier.sent` →
`.msgs`, `repo.get_trigger` → 커널 `execution.ledger.get_trigger`, `tools` → fo `tools`."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from tests.features.fo._gate import position, quote
from tests.features.fo._support import (
    ACCT,
    _held_position,
    _open_with_live_entry,
    _pending_flag,
    at,
    balance,
    make_ctx,
)
from tests.features.fo._support import _engine_market as _market
from tests.features.fo._support import settings as fo_settings
from trader.features.fo import engine, executor, store, tools, triggers
from trader.kernel.execution import ledger
from trader.kernel.ledger import control

DAY = date(2026, 10, 27)  # 화요일


def _ctx(conn, now, market=None, **fo):
    fo.setdefault("reeval", {"periodic_max_per_day": 0})
    return make_ctx(conn, now=now, market=market or _market(now), **fo)


def _tick(ctx, calls=None):
    sink = calls if calls is not None else []
    return engine.tick(ctx, request_reeval=lambda c, p: sink.append(p))


def _orders(ctx):
    return [(o.side, o.qty) for o in ctx.brokers[ACCT].orders]


def test_monitor_runs_from_0845_futures_stop_only(conn):
    now = at(8, 50)
    ctx = _ctx(conn, now, _market(now, idx="340"))  # 지수는 손절선 아래지만 09:00 전이라 무시
    _held_position(conn, ctx, qty=2, stop_fut=Decimal("346"))
    _tick(ctx)
    assert _orders(ctx) == []
    ctx.market_client = _market(now, idx="340", fut="345.5")
    out = _tick(ctx)
    assert _orders(ctx) == [("SELL", 2)]
    assert out["accounts"][ACCT]["exits"][0]["intent"] == "STOP"


def test_index_stale_uses_futures_only(conn):
    now = at(10, 30)
    ctx = _ctx(conn, now, _market(now, idx="344", idx_age_s=600))
    _held_position(conn, ctx, qty=2, stop_fut=Decimal("340"))
    _tick(ctx)
    assert _orders(ctx) == []
    ctx.market_client = _market(now, idx="344")
    _tick(ctx)
    assert _orders(ctx) == [("SELL", 2)]


def _short_call(conn, ctx, *, premium_stop):
    opt = quote(code="205W11350", kind="CALL", price="2.1", delta=0.5, iv=18.0,
                strike=Decimal("350"), basis=None)  # fmt: skip
    pos = _held_position(conn, ctx, qty=2, code="205W11350", kind="CALL", contract_side="SHORT",
                         direction="SHORT", vehicle="OPTION_SELL", stop_pt=Decimal("360"),
                         entry_premium=Decimal("1.5"), approved_mult=2.0,
                         premium_stop=premium_stop)  # fmt: skip
    return opt, pos


def test_option_stop_uses_stored_premium_stop_not_ratio(conn):
    now = at(10, 30)
    ctx = _ctx(conn, now)
    opt, pos = _short_call(conn, ctx, premium_stop=Decimal("2.0"))
    ctx.market_client = _market(now, opt=opt.model_copy(update={"price": Decimal("1.9")}))
    _tick(ctx)
    assert _orders(ctx) == []
    ctx.market_client = _market(now, opt=opt)  # 2.1 ≥ 저장선 2.0 (평균가 × 배수 3.0 이 아님)
    _tick(ctx)
    assert _orders(ctx) == [("BUY", 2)]


def test_maintain_leverage_breach_reduces(conn):
    now = at(10, 30)
    ctx = _ctx(conn, now, max_contracts=20)
    pos = _held_position(conn, ctx, qty=12, qty_target=12)  # 12 × 1,750만 = 2.1억 > 2배
    out = _tick(ctx)
    assert _orders(ctx) == [("SELL", 1)]
    assert out["accounts"][ACCT]["maintain"][0]["target"] == 11
    assert store.get_position(conn, pos.id).qty_target == 11


def test_maintain_orders_option_sell_first(conn):
    ctx = _ctx(conn, at(10, 30))
    fut = position(id=1, qty_held=1)
    sell = position(id=2, code="205W11350", kind="CALL", contract_side="SHORT",
                    direction="SHORT", vehicle="OPTION_SELL", qty_held=1)  # fmt: skip
    bal = balance(add_margin=Decimal("1"))
    acts, _ = engine.maintain(ctx, executor.account_of(ctx, ACCT), [fut, sell], bal=bal,
                              quotes={}, index=Decimal("350"), now=at(10, 30),
                              today=DAY)  # fmt: skip
    assert acts[0][0].id == 2 and acts[0][1] == 0


def test_maintain_stress_breach_at_1500(conn):
    now = at(14, 30)
    ctx = _ctx(conn, now, hold_overnight=True, overnight_risk_pct=0.001)
    _held_position(conn, ctx, qty=2)
    _tick(ctx)
    assert _orders(ctx) == []
    assert any("유지 조건" in m for m in ctx.notifier.msgs)
    ctx.now = lambda: at(15, 0)
    ctx.market_client = _market(at(15, 0))
    _tick(ctx)
    assert _orders(ctx) == [("SELL", 1)]


def test_halted_recalls_entries(conn):
    now = at(10, 30)
    ctx = _ctx(conn, now)
    _open_with_live_entry(conn, ctx)
    b = ctx.brokers[ACCT].balance
    ctx.brokers[ACCT].balance = b.model_copy(
        update={"cash": b.cash.model_copy(update={"realized_today": Decimal("-4000000")})}
    )
    out = _tick(ctx)
    acc = out["accounts"][ACCT]
    assert acc["halted"] is True and acc["recalled"][0]["cancelled"] == 1
    assert control.get_flag(conn, executor.halted_key(ACCT)) == DAY.isoformat()
    assert any("일일 손실" in m for m in ctx.notifier.msgs)


def test_enabled_false_recalls_entries(conn):
    now = at(10, 30)
    ctx = _ctx(conn, now)
    _open_with_live_entry(conn, ctx)
    ctx.settings = ctx.settings.model_copy(
        update={"fo": ctx.settings.fo.model_copy(update={"enabled": False})}
    )
    out = _tick(ctx)
    assert out["accounts"][ACCT]["recalled"][0]["reason"] == "fo.enabled=false"


def test_force_close_day_0900(conn):
    ctx = _ctx(conn, at(8, 55), roll_days=2)
    _held_position(conn, ctx, qty=2, last_trade_date="2026-10-29")  # 목요일 → D-2 = 화요일
    _tick(ctx)
    assert _orders(ctx) == []
    ctx.now = lambda: at(9, 0)
    ctx.market_client = _market(at(9, 0))
    out = _tick(ctx)
    assert _orders(ctx) == [("SELL", 2)]
    assert out["accounts"][ACCT]["exits"][0]["intent"] == "EXPIRY"


def test_hold_overnight_false_daily_close(conn):
    ctx = _ctx(conn, at(15, 30))
    _held_position(conn, ctx, qty=2)
    _tick(ctx)
    assert _orders(ctx) == []
    ctx.now = lambda: at(15, 35)
    ctx.market_client = _market(at(15, 35))
    out = _tick(ctx)
    assert out["accounts"][ACCT]["exits"][0]["intent"] == "SESSION_CLOSE"
    assert _orders(ctx) == [("SELL", 2)]


def test_hold_overnight_true_no_daily_close(conn):
    ctx = _ctx(conn, at(15, 40), hold_overnight=True)
    _held_position(conn, ctx, qty=2)
    _tick(ctx)
    assert _orders(ctx) == []


def test_expiry_forced_close_discards_pending_reeval(conn):
    ctx = _ctx(conn, at(9, 10), roll_days=2)
    pos = _held_position(conn, ctx, qty=2, last_trade_date="2026-10-29")
    tid = triggers.record(conn, pos, triggers.Trigger("news", {}, None), now=at(9, 5))
    _tick(ctx)
    row = conn.execute("SELECT handled_at, session_id FROM triggers WHERE id = ?",
                       (tid,)).fetchone()  # fmt: skip
    assert row["handled_at"] and row["session_id"].startswith("폐기")


def test_pending_entry_runs_in_window_and_discards_after_cutoff(conn):
    ctx = _ctx(conn, at(8, 55))
    _pending_flag(conn)
    _tick(ctx)
    assert _orders(ctx) == []  # 진입 창(09:00) 전에는 기다린다
    assert control.get_flag(conn, f"{tools.FO_PENDING_FLAG}{ACCT}") is not None
    ctx.now = lambda: at(9, 5)
    ctx.brokers[ACCT]._now = lambda: at(9, 5)  # 잔고 요청 시각도 09:05(실행 직전 재조회)
    ctx.market_client = _market(at(9, 5))
    out = _tick(ctx)
    res = out["accounts"][ACCT]["entries"][0]
    assert res["pending"] == "r1" and res["results"][0]["status"] == "accepted", res
    assert [o.side for o in ctx.brokers[ACCT].orders] == ["BUY"]
    assert control.get_flag(conn, f"{tools.FO_PENDING_FLAG}{ACCT}") is None


def test_pending_entry_cutoff_discard(conn):
    ctx = _ctx(conn, at(15, 32))
    _pending_flag(conn)
    out = _tick(ctx)
    assert "cutoff" in out["accounts"][ACCT]["entries"][0]["discarded"]
    assert _orders(ctx) == []
    assert control.get_flag(conn, f"{tools.FO_PENDING_FLAG}{ACCT}") is None


def test_snapshot_written_without_positions(conn):
    ctx = _ctx(conn, at(10, 30))
    out = _tick(ctx)
    assert out["accounts"][ACCT]["snapshot"] is True
    n = conn.execute("SELECT COUNT(*) FROM fo_nav_snapshot WHERE account_id = ?",
                     (ACCT,)).fetchone()[0]  # fmt: skip
    assert n == 1


def test_trigger_records_and_requests_reeval(conn):
    ctx = _ctx(conn, at(10, 30), reeval={"periodic_max_per_day": 4})
    pos = _held_position(conn, ctx, qty=2)
    calls: list[dict] = []
    out = _tick(ctx, calls)
    t = out["accounts"][ACCT]["triggers"][0]
    assert t["kind"] == "periodic" and calls and calls[0]["position_id"] == pos.id
    assert calls[0]["trigger_id"] == t["trigger_id"] and calls[0]["round"] == 1
    assert ledger.get_trigger(conn, t["trigger_id"])["kind"] == "fo:periodic"
    _tick(ctx, calls)
    assert len(calls) == 1  # 미처리 트리거가 있으면 새로 내지 않는다


def test_awaiting_settlement_settles_when_gone(conn):
    ctx = _ctx(conn, at(10, 30))
    pos = _held_position(conn, ctx, qty=2, last_trade_date="2026-10-26")
    with store.tx(conn):
        store.transition(conn, pos.id, expected_version=pos.version, status="awaiting_settlement")
    ctx.brokers[ACCT].balance = balance()
    _tick(ctx)
    assert store.get_position(conn, pos.id).status == "settled"


def test_exit_reason_pure_cases():
    cfg = fo_settings().fo
    pos = position(stop_pt=Decimal("345"), tp_pt=Decimal("360"))
    assert engine.exit_reason(pos, cfg=cfg, index=Decimal("361"), quote=None, now=at(10, 0),
                              today=DAY)[0] == "CLOSE"  # fmt: skip
    assert engine.exit_reason(pos, cfg=cfg, index=None, quote=None, now=at(10, 0),
                              today=DAY) is None  # fmt: skip
    short = position(direction="SHORT", contract_side="SHORT", stop_pt=Decimal("355"))
    assert engine.exit_reason(short, cfg=cfg, index=Decimal("356"), quote=None, now=at(10, 0),
                              today=DAY)[0] == "STOP"  # fmt: skip


def test_tick_commits_open_transaction_before_broker_calls(conn):
    """Ruling 4: 호출자가 남긴 암묵 트랜잭션은 틱 진입부에서 commit(브로커 호출이 쓰기 잠금 밖)."""
    ctx = _ctx(conn, at(10, 30))
    conn.execute("INSERT INTO control(key, value, updated_at) VALUES ('t7probe', '1', 'x')")
    assert conn.in_transaction
    seen: list[bool] = []
    broker = ctx.brokers[ACCT]
    orig = broker.get_balance

    def spy():
        seen.append(conn.in_transaction)
        return orig()

    broker.get_balance = spy
    _tick(ctx)
    assert seen and seen[0] is False


def test_novel_news_reads_through_inputs_port(conn):
    """`ports["inputs"]` 가 있으면 `_novel_news` 는 raw SQL 대신 `inputs.recent`(novel_only·
    untagged_only — 종목 태그 없는 시장·거시 뉴스)로 읽어 센다."""
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 10, 27, 1, 30, tzinfo=UTC)
    calls: list = []

    class Inputs:
        def recent(self, conn, market, *, tickers, since, limit=20, novel_only=False, asof=None,
                   untagged_only=False):  # fmt: skip
            calls.append((market, tickers, since, novel_only, untagged_only, asof))
            return [{"tickers": []}, {"tickers": []}]

    assert engine._novel_news(conn, now, 30, Inputs()) == 2
    assert calls == [("KR", None, now - timedelta(minutes=30), True, True, now)]
