"""단타 청산(옛 전체 검토 O6·O7): 허용 판정보다 먼저, 계좌별 체결 수량만, 실패·미체결은 재시도.

옛 `tests/scalp/test_engine_exit.py` 20건 중 19건 이식(`test_legacy_state_without_legs_sells_once_
with_cap` 은 Ruling A3 로 제외). 계좌는 옛 `_ctx` 기본과 같게 모의 1개(paper-main) + 실전 off 1개로
덮는다 — 공용 conftest 의 두 번째 모의 계좌가 옛 단언(주문 수·취소 목록)을 바꾸지 않게."""

from datetime import timedelta
from decimal import Decimal

import pytest

from tests.features.accounts.conftest import make_account
from tests.features.scalp.conftest import NOW, D, bars, scan_checkpoint
from trader.features.accounts import modes
from trader.features.regime import regime as regime_mod
from trader.features.scalp import engine, exits
from trader.features.scalp import state as scalp_state
from trader.features.scalp.state import load_state, save_state
from trader.kernel.broker.models import Fill, Position
from trader.kernel.execution import ledger as xledger
from trader.kernel.ledger.control import set_flag
from trader.kernel.session.run_store import read_checkpoint, run_dir

T = "005930"
TWO_PAPER = [
    make_account("paper-main", "paper", "auto", ["KR"]),
    make_account("paper-two", "paper", "auto", ["KR"]),
]


@pytest.fixture
def accounts():
    """옛 `_ctx` 기본 계좌: 모의 auto 1개 + 실전 off 1개."""
    return [
        make_account("paper-main", "paper", "auto", ["KR"]),
        make_account("real-main", "real", "off", ["KR"]),
    ]


def _ctx(scalp_ctx, market, **kw):
    market.minutes[T] = bars(burst=True)
    market.prices[T] = Decimal("10050")
    return scalp_ctx(**kw)


def _st(project, d=D):
    return load_state(project / "state", d, "KR")


def _pos(qty: int, price: str = "10050") -> Position:
    p = Decimal(price)
    return Position(
        ticker=T,
        market="KR",
        qty=qty,
        avg_price=p,
        current_price=p,
        eval_amount=p * qty,
        pnl_pct=0.0,
    )


def _fill(order_no: str, side: str, qty: int, price: str, minutes: int = 1) -> Fill:
    return Fill(
        order_no=order_no,
        ticker=T,
        market="KR",
        side=side,  # type: ignore[arg-type]
        qty=qty,
        price=Decimal(price),
        filled_at=NOW + timedelta(minutes=minutes),
    )


def _advance(ctx, minutes: int) -> None:
    t = NOW + timedelta(minutes=minutes)
    ctx.now = lambda: t


def _enter(ctx, project):
    scan_checkpoint(project, "KR", [T])
    out = engine.tick(ctx, market="KR", today=D)
    assert [e["ticker"] for e in out["entries"]] == [T]
    return _st(project)["open"][0]


def _sells(conn, account="paper-main"):
    return conn.execute(
        "SELECT qty, status FROM orders WHERE side='SELL' AND account_id=? ORDER BY id", (account,)
    ).fetchall()


def test_entry_accepted_is_pending_until_filled(scalp_ctx, market, brokers, project):
    ctx = _ctx(scalp_ctx, market)
    pos = _enter(ctx, project)
    assert pos["status"] == "pending_entry"
    leg = pos["legs"]["paper-main"]
    assert leg["req"] == 49 and len(leg["buy"]) == 1
    # 체결 동기화 뒤 보유로
    brokers["paper-main"].fills[("KR", D)] = [_fill("FAKE-1", "BUY", 49, "10040")]
    _advance(ctx, 1)
    engine.tick(ctx, market="KR", today=D)
    pos = _st(project)["open"][0]
    assert pos["status"] == "held" and pos["filled"] == {"paper-main": 49}


def test_stop_exit_runs_after_bear_switch_and_ret_uses_fill_prices(
    scalp_ctx, market, brokers, conn, project
):
    ctx = _ctx(scalp_ctx, market)
    assert ctx.ports["regime"].hedge_cfg().enabled  # 옛 HedgeCfg(enabled=True) 와 같은 전제
    _enter(ctx, project)
    b = brokers["paper-main"]
    b.fills[("KR", D)] = [_fill("FAKE-1", "BUY", 49, "10000")]
    b.positions = [_pos(49)]
    set_flag(conn, f"{regime_mod.FLAG_PREFIX}KR", {"state": "bear", "count": 3})
    market.prices[T] = Decimal("9800")  # 손절 −1% 아래
    _advance(ctx, 3)
    out = engine.tick(ctx, market="KR", today=D)
    assert "bear" in (out.get("skipped") or out["entries_blocked"])  # 신규 진입은 막히지만
    assert [r["qty"] for r in _sells(conn)] == [49]  # 청산은 나간다
    st = _st(project)
    assert st["open"][0]["status"] == "closing" and st["closed"] == []
    # 청산 체결 → 체결가 기준 실현손익
    b.fills[("KR", D)].append(_fill("FAKE-2", "SELL", 49, "9850", minutes=4))
    _advance(ctx, 4)
    out = engine.tick(ctx, market="KR", today=D)
    assert out["exits"][0]["reason"] == "stop" and abs(out["exits"][0]["ret"] + 0.015) < 1e-6
    st = _st(project)
    assert st["open"] == [] and st["closed"][0]["exit_price"] == "9850"


@pytest.mark.parametrize(
    "accounts",
    [
        [
            make_account("paper-main", "paper", "auto", ["KR"]),
            make_account("real-1", "real", "auto", ["KR"]),
        ]
    ],
)
def test_exit_sent_after_switch_turned_off(scalp_ctx, market, brokers, accounts, conn, project):
    """스위치는 신규 진입만 막는다 — 다리가 있는 계좌의 스위치를 끈 뒤에도 청산(손절)은 나간다."""
    from trader.features.accounts import api as accounts_api
    from trader.features.scalp import switch

    real = next(a for a in accounts if a.id == "real-1")
    modes.set_account_mode(conn, real, "auto", confirm=accounts_api.LIVE_CONFIRM)
    set_flag(conn, switch.flag_key("real-1"), True)
    ctx = _ctx(scalp_ctx, market)
    pos = _enter(ctx, project)
    assert set(pos["legs"]) == {"paper-main", "real-1"}
    for acc in ("paper-main", "real-1"):
        no = conn.execute(
            "SELECT order_no FROM orders WHERE side='BUY' AND account_id=?", (acc,)
        ).fetchone()["order_no"]
        brokers[acc].fills[("KR", D)] = [_fill(no, "BUY", 49, "10000")]
        brokers[acc].positions = [_pos(49)]
    set_flag(conn, switch.flag_key("paper-main"), False)
    set_flag(conn, switch.flag_key("real-1"), False)
    assert engine.skip_accounts(ctx, "KR") >= {"paper-main", "real-1"}  # 진입 skip 에는 들어간다
    market.prices[T] = Decimal("9800")  # 손절 −1% 아래
    _advance(ctx, 3)
    engine.tick(ctx, market="KR", today=D)
    assert [r["qty"] for r in _sells(conn, "paper-main")] == [49]
    assert [r["qty"] for r in _sells(conn, "real-1")] == [49]


@pytest.mark.parametrize("accounts", [TWO_PAPER])
def test_settle_pending_skips_switched_off_account(scalp_ctx, settings, market, conn, project):
    """판정 회수 진입은 진입용 skip(스위치 반영) — 판정 대기 중 스위치를 끈 계좌엔 다리가 없다."""
    from tests.features.scalp.conftest import SyncJudge
    from trader.features.scalp import switch
    from trader.features.scalp.schemas import ScalpVerdict
    from trader.features.scalp.settings import ScalpCfg

    go = ScalpVerdict(
        go=True, size="half", take_profit_pct=0.02, stop_pct=0.02, max_hold_min=120,
        reason="돌파 확인", confidence=0.7,
    )  # fmt: skip
    cfg = ScalpCfg(enabled=True, judge=True)
    ctx = _ctx(scalp_ctx, market, settings=settings.model_copy(update={"scalp": cfg}))
    ctx.judge = SyncJudge(go)
    scan_checkpoint(project, "KR", [T])
    assert engine.tick(ctx, market="KR", today=D)["entries"] == []  # 제출만
    assert T in _st(project)["pending"]
    set_flag(conn, switch.flag_key("paper-two"), False)
    out = engine.tick(ctx, market="KR", today=D)
    assert [e["ticker"] for e in out["entries"]] == [T]
    assert set(_st(project)["open"][0]["legs"]) == {"paper-main"}


@pytest.mark.parametrize("accounts", [TWO_PAPER])
def test_exit_skips_accounts_that_did_not_enter(scalp_ctx, market, brokers, conn, project):
    ctx = _ctx(scalp_ctx, market)
    brokers["paper-two"].fail = {"place_order"}
    pos = _enter(ctx, project)
    assert set(pos["legs"]) == {"paper-main"}
    brokers["paper-two"].fail = set()
    brokers["paper-main"].fills[("KR", D)] = [_fill("FAKE-1", "BUY", 49, "10050")]
    brokers["paper-main"].positions = [_pos(49)]
    brokers["paper-two"].positions = [_pos(30)]  # 일반 보유분: 단타와 무관
    market.prices[T] = Decimal("9900")
    _advance(ctx, 3)
    engine.tick(ctx, market="KR", today=D)
    assert [r["qty"] for r in _sells(conn)] == [49]
    assert _sells(conn, "paper-two") == []
    d = run_dir(D, "KR", T, project / "state")
    assert read_checkpoint(d, "47_scalp_exit_paper-two") is None


def test_exit_failure_keeps_closing_and_retries(scalp_ctx, market, brokers, conn, project):
    ctx = _ctx(scalp_ctx, market)
    _enter(ctx, project)
    b = brokers["paper-main"]
    b.fills[("KR", D)] = [_fill("FAKE-1", "BUY", 49, "10050")]
    b.positions = [_pos(49)]
    market.prices[T] = Decimal("9900")
    b.fail = {"place_order"}
    _advance(ctx, 3)
    out = engine.tick(ctx, market="KR", today=D)
    assert out["exits"] == []
    st = _st(project)
    assert st["open"][0]["status"] == "closing" and st["closed"] == []
    assert [r["status"] for r in _sells(conn)] == ["failed"]
    # 다음 틱: 가격이 손절선 위로 돌아와도 청산을 이어 간다
    b.fail = set()
    market.prices[T] = Decimal("10060")
    _advance(ctx, 4)
    engine.tick(ctx, market="KR", today=D)
    assert [r["status"] for r in _sells(conn)] == ["failed", "accepted"]
    assert _st(project)["open"][0]["status"] == "closing"


def test_exit_cancels_unfilled_buy_then_sells_only_filled_part(
    scalp_ctx, market, brokers, conn, project
):
    ctx = _ctx(scalp_ctx, market)
    _enter(ctx, project)
    b = brokers["paper-main"]
    b.fills[("KR", D)] = [_fill("FAKE-1", "BUY", 20, "10050")]  # 49주 중 20주만 체결
    b.positions = [_pos(120)]  # 일반 보유 100 + 단타 20
    market.prices[T] = Decimal("9900")
    _advance(ctx, 3)
    engine.tick(ctx, market="KR", today=D)
    assert b.cancelled == ["FAKE-1"]
    buy = conn.execute("SELECT status FROM orders WHERE side='BUY'").fetchone()
    assert buy["status"] == "cancelled"
    assert [r["qty"] for r in _sells(conn)] == [20]  # 일반 보유 100주는 건드리지 않는다


def test_exit_without_any_fill_cancels_buy_and_voids_position(
    scalp_ctx, market, brokers, conn, project
):
    ctx = _ctx(scalp_ctx, market)
    _enter(ctx, project)
    b = brokers["paper-main"]
    market.prices[T] = Decimal("9900")
    _advance(ctx, 3)
    engine.tick(ctx, market="KR", today=D)
    assert b.cancelled == ["FAKE-1"] and _sells(conn) == []
    # 취소 직전 체결이 있었을 수 있어 한 번 더 체결을 확인한 뒤에야 소멸시킨다
    assert _st(project)["open"][0]["legs"]["paper-main"]["recheck"] == ["buy"]
    _advance(ctx, 4)
    out = engine.tick(ctx, market="KR", today=D)
    st = _st(project)
    assert st["open"] == [] and st["closed"] == [] and out["exits"] == []


def test_fill_right_before_cancel_is_sold_not_voided(scalp_ctx, market, brokers, conn, project):
    """취소 직전 체결(원장 반영 전)을 놓치지 않는다: 다음 틱 동기화가 찾은 체결분을 판다."""
    ctx = _ctx(scalp_ctx, market)
    _enter(ctx, project)
    b = brokers["paper-main"]
    market.prices[T] = Decimal("9900")
    _advance(ctx, 3)
    engine.tick(ctx, market="KR", today=D)  # 체결 0 으로 보이는 채 BUY 취소
    b.fills[("KR", D)] = [_fill("FAKE-1", "BUY", 10, "10050", minutes=3)]
    b.positions = [_pos(10)]
    _advance(ctx, 4)
    engine.tick(ctx, market="KR", today=D)
    assert [r["qty"] for r in _sells(conn)] == [10]


def test_sell_cancelled_with_unseen_fill_is_not_resold(scalp_ctx, market, brokers, conn, project):
    """지정가 청산을 취소했는데 취소 직전 체결이 있었다면 그 수량을 다시 팔지 않는다."""
    ctx = _ctx(scalp_ctx, market)
    _enter(ctx, project)
    b = brokers["paper-main"]
    b.fills[("KR", D)] = [_fill("FAKE-1", "BUY", 49, "10050")]
    b.positions = [_pos(149)]  # 일반 보유 100
    market.prices[T] = Decimal("9900")
    _advance(ctx, 3)
    engine.tick(ctx, market="KR", today=D)
    sell = conn.execute("SELECT id FROM orders WHERE side='SELL'").fetchone()["id"]
    conn.execute("UPDATE orders SET order_type='LIMIT' WHERE id=?", (sell,))  # US 지정가처럼
    conn.commit()
    _advance(ctx, 3 + exits.EXIT_REQUOTE_S // 60 + 1)
    engine.tick(ctx, market="KR", today=D)  # 오래된 지정가 취소(체결은 아직 모름)
    assert b.cancelled == ["FAKE-2"] and len(_sells(conn)) == 1
    b.fills[("KR", D)].append(_fill("FAKE-2", "SELL", 49, "9900", minutes=5))
    _advance(ctx, 10)
    out = engine.tick(ctx, market="KR", today=D)
    assert len(_sells(conn)) == 1  # 다시 팔지 않고 끝낸다
    assert out["exits"] and out["exits"][0]["reason"] == "stop"


def test_unrecorded_exit_order_is_adopted_not_resubmitted(
    scalp_ctx, market, brokers, conn, project
):
    """청산 접수 뒤 상태 저장 전에 끊긴 경우: 원장의 결정 없는 SELL 을 흡수해 다시 내지 않는다."""
    ctx = _ctx(scalp_ctx, market)
    _enter(ctx, project)
    b = brokers["paper-main"]
    b.fills[("KR", D)] = [_fill("FAKE-1", "BUY", 49, "10050")]
    b.positions = [_pos(149)]
    market.prices[T] = Decimal("9900")
    _advance(ctx, 3)
    engine.tick(ctx, market="KR", today=D)
    st = _st(project)
    st["open"][0]["legs"]["paper-main"]["sell"] = []  # 저장 전에 끊긴 것처럼
    save_state(project / "state", D, "KR", st)
    _advance(ctx, 4)
    engine.tick(ctx, market="KR", today=D)
    assert len(_sells(conn)) == 1


def test_unfinished_position_carries_over_to_next_day(scalp_ctx, market, brokers, conn, project):
    """마감까지 못 판 단타는 다음 운영일 첫 틱에 승계돼 청산을 이어 간다."""
    ctx = _ctx(scalp_ctx, market)
    _enter(ctx, project)
    b = brokers["paper-main"]
    b.fills[("KR", D)] = [_fill("FAKE-1", "BUY", 49, "10050")]
    b.positions = [_pos(49)]
    _advance(ctx, 1)
    engine.tick(ctx, market="KR", today=D)  # held, 청산 신호 없음
    nxt = D + timedelta(days=2)
    _advance(ctx, 2 * 24 * 60)
    out = engine.tick(ctx, market="KR", today=nxt)
    assert [r["qty"] for r in _sells(conn)] == [49]
    assert _st(project)["open"] == []
    pos = _st(project, nxt)["open"][0]
    assert pos["exit_reason"] == "carry_over" and pos["carried_from"] == D.isoformat()
    assert out["entries"] == []


def test_paused_still_exits_open_position(scalp_ctx, market, brokers, conn, project):
    """일시정지 중에도 손절 조건이면 SELL 이 나간다(리스크 청산) — 신규 진입만 막힌다."""
    ctx = _ctx(scalp_ctx, market)
    _enter(ctx, project)
    b = brokers["paper-main"]
    b.fills[("KR", D)] = [_fill("FAKE-1", "BUY", 49, "10050")]
    b.positions = [_pos(49)]
    modes.set_paused(conn, True)
    market.prices[T] = Decimal("9900")
    _advance(ctx, 3)
    out = engine.tick(ctx, market="KR", today=D)
    assert "skipped" not in out and out["entries_blocked"] == "일시정지"  # 청산이 돈 틱
    assert [r["qty"] for r in _sells(conn)] == [49]
    notes = [m for m in ctx.notifier.msgs if "일시정지 중이지만 단타 청산" in m]
    assert len(notes) == 1 and "005930" in notes[0]
    res = [{"approved": True, "account_id": "paper-main", "order": {"order_id": 99}}]
    exits.notify_paused_exit(ctx, "KR", D, {"ticker": T}, {"paper-main": {}}, res)
    notes = [m for m in ctx.notifier.msgs if "일시정지 중이지만 단타 청산" in m]
    assert len(notes) == 1  # 하루 1회


def test_unpaused_exit_sends_no_paused_notice(scalp_ctx, market, brokers, conn, project):
    ctx = _ctx(scalp_ctx, market)
    _enter(ctx, project)
    b = brokers["paper-main"]
    b.fills[("KR", D)] = [_fill("FAKE-1", "BUY", 49, "10050")]
    b.positions = [_pos(49)]
    market.prices[T] = Decimal("9900")
    _advance(ctx, 3)
    engine.tick(ctx, market="KR", today=D)
    assert [r["qty"] for r in _sells(conn)] == [49]
    assert not [m for m in ctx.notifier.msgs if "일시정지" in m]


def test_paused_blocks_new_scalp_entry(scalp_ctx, market, brokers, conn, project):
    ctx = _ctx(scalp_ctx, market)
    modes.set_paused(conn, True)
    scan_checkpoint(project, "KR", [T])
    out = engine.tick(ctx, market="KR", today=D)
    assert out["skipped"] == "일시정지" and out["entries"] == []  # 청산이 없으면 skipped
    assert conn.execute("SELECT COUNT(*) FROM orders WHERE side='BUY'").fetchone()[0] == 0


def test_paused_approve_mode_account_does_not_sell_or_request_approval(
    scalp_ctx, market, brokers, conn, project, accounts
):
    """can_trade 의 auto 요구: 승인 모드 계좌는 일시정지 중에도 매도·승인 요청을 만들지 않는다."""
    ctx = _ctx(scalp_ctx, market)
    _enter(ctx, project)
    b = brokers["paper-main"]
    b.fills[("KR", D)] = [_fill("FAKE-1", "BUY", 49, "10050")]
    b.positions = [_pos(49)]
    modes.set_account_mode(conn, accounts[0], "approve")
    modes.set_paused(conn, True)
    market.prices[T] = Decimal("9900")
    _advance(ctx, 3)
    engine.tick(ctx, market="KR", today=D)
    assert _sells(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0


def test_entering_position_adopts_ledger_buy_after_crash(scalp_ctx, market, brokers, project):
    """진입 접수 뒤 상태 저장 전에 끊긴 경우: 임시 포지션이 원장의 BUY 를 흡수해 추적한다."""
    ctx = _ctx(scalp_ctx, market)
    _enter(ctx, project)
    st = _st(project)
    st["open"][0] |= {"status": "entering", "legs": {}, "qty": {}}  # 저장 전에 끊긴 것처럼
    save_state(project / "state", D, "KR", st)
    brokers["paper-main"].fills[("KR", D)] = [_fill("FAKE-1", "BUY", 49, "10050")]
    _advance(ctx, 1)
    engine.tick(ctx, market="KR", today=D)
    pos = _st(project)["open"][0]
    assert pos["status"] == "held" and pos["filled"] == {"paper-main": 49}
    assert len(brokers["paper-main"].orders) == 1  # 다시 사지 않았다


def test_adopt_entry_orders_ignores_other_origins(scalp_ctx, market, brokers, conn, project):
    """진입 흡수는 origin='scalp' 주문만 — 같은 종목의 결정 없는 다른 BUY(rebalance 등)는 제외."""
    ctx = _ctx(scalp_ctx, market)
    pos = _enter(ctx, project)
    scalp_id = pos["legs"]["paper-main"]["buy"][0]
    other = xledger.insert_order(
        conn, decision_id=None, account_id="paper-main", ticker=T, market="KR", side="BUY",
        qty=7, order_type="MARKET", price=None, status="accepted", order_no="OTHER-1",
        message="", mode="auto", created_at=ctx.now().isoformat(), origin="rebalance",
    )  # fmt: skip
    assert other != scalp_id
    entering = {"ticker": T, "entry_at": pos["entry_at"], "status": "entering"}
    exits.adopt_entry_orders(ctx, entering)
    assert entering["legs"]["paper-main"]["buy"] == [scalp_id]
    assert entering["qty"] == {"paper-main": pos["legs"]["paper-main"]["req"]}


def test_failed_fill_query_keeps_recheck(scalp_ctx, market, brokers, project):
    """취소 뒤 체결 조회가 실패하면 재확인을 지우지 않는다(실패를 '체결 없음' 으로 보지 않음)."""
    ctx = _ctx(scalp_ctx, market)
    _enter(ctx, project)
    b = brokers["paper-main"]
    market.prices[T] = Decimal("9900")
    _advance(ctx, 3)
    engine.tick(ctx, market="KR", today=D)  # BUY 취소 → recheck
    b.fail = {"get_fills"}
    _advance(ctx, 4)
    engine.tick(ctx, market="KR", today=D)
    pos = _st(project)["open"][0]
    assert pos["legs"]["paper-main"]["recheck"] == ["buy"]
    b.fail = set()
    _advance(ctx, 5)
    engine.tick(ctx, market="KR", today=D)
    assert _st(project)["open"] == []


def test_carry_over_is_idempotent_and_syncs_original_day(scalp_ctx, market, brokers, conn, project):
    """이월이 끊겨 옛 파일에 남아 있어도 중복 승계하지 않고, 전일 주문일로 체결을 다시 읽는다."""
    ctx = _ctx(scalp_ctx, market)
    _enter(ctx, project)
    old = _st(project)
    b = brokers["paper-main"]
    b.fills[("KR", D)] = [_fill("FAKE-1", "BUY", 49, "10050")]  # 전일 체결(동기화 누락분)
    b.positions = [_pos(49)]
    nxt = D + timedelta(days=2)
    _advance(ctx, 2 * 24 * 60)
    engine.tick(ctx, market="KR", today=nxt)  # 전일 주문일로 체결을 읽어 49주 청산
    assert [r["qty"] for r in _sells(conn)] == [49]
    b.fills[("KR", nxt)] = [_fill("FAKE-2", "SELL", 49, "9900", minutes=2 * 24 * 60)]
    _advance(ctx, 2 * 24 * 60 + 1)
    engine.tick(ctx, market="KR", today=nxt)
    assert _st(project, nxt)["open"] == []
    save_state(project / "state", D, "KR", old)  # 옛 파일을 비우기 전에 끊긴 것처럼 되살림
    _advance(ctx, 2 * 24 * 60 + 2)
    engine.tick(ctx, market="KR", today=nxt)
    st = _st(project, nxt)
    assert st["open"] == [] and len(st["closed"]) == 1  # 다시 승계·이중 집계하지 않는다
    assert len(_sells(conn)) == 1


def test_recheck_is_saved_before_cancel_call(scalp_ctx, market, brokers, project, monkeypatch):
    """취소 요청 시점에 재확인 표시가 이미 파일에 있다(취소 뒤 끊겨도 체결을 다시 읽음)."""
    ctx = _ctx(scalp_ctx, market)
    _enter(ctx, project)
    b = brokers["paper-main"]
    seen = []
    orig = b.cancel_order

    def cancel(order_no, **kw):
        seen.append(_st(project)["open"][0]["legs"]["paper-main"].get("recheck"))
        return orig(order_no, **kw)

    monkeypatch.setattr(b, "cancel_order", cancel)
    market.prices[T] = Decimal("9900")
    _advance(ctx, 3)
    engine.tick(ctx, market="KR", today=D)
    assert seen == [["buy"]]


def test_entering_position_carried_over_adopts_entry(scalp_ctx, market, brokers, conn, project):
    """진입 접수 뒤 저장 전에 끊긴 채(entering) 다음 날로 넘어와도 진입 주문을 흡수해 청산한다."""
    ctx = _ctx(scalp_ctx, market)
    _enter(ctx, project)
    st = _st(project)
    st["open"][0] |= {"status": "entering", "legs": {}, "qty": {}}
    save_state(project / "state", D, "KR", st)
    b = brokers["paper-main"]
    b.fills[("KR", D)] = [_fill("FAKE-1", "BUY", 49, "10050")]
    b.positions = [_pos(49)]
    nxt = D + timedelta(days=2)
    _advance(ctx, 2 * 24 * 60)
    engine.tick(ctx, market="KR", today=nxt)
    assert [r["qty"] for r in _sells(conn)] == [49]


def _held_closing(ctx, project, brokers, market):
    _enter(ctx, project)
    b = brokers["paper-main"]
    b.fills[("KR", D)] = [_fill("FAKE-1", "BUY", 20, "10050")]
    b.positions = [_pos(120)]
    market.prices[T] = Decimal("9900")
    return b


def test_no_cancel_or_sell_when_account_not_auto(
    scalp_ctx, market, brokers, conn, project, accounts
):
    """auto 가 아닌 계좌(approve·off)·dry-run 전환 뒤에는 취소도 매도도, 승인 요청도 내지 않는다."""
    ctx = _ctx(scalp_ctx, market)
    b = _held_closing(ctx, project, brokers, market)
    modes.set_account_mode(conn, accounts[0], "approve")
    _advance(ctx, 3)
    engine.tick(ctx, market="KR", today=D)
    _advance(ctx, 4)
    engine.tick(ctx, market="KR", today=D)
    assert b.cancelled == [] and _sells(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
    assert _st(project)["open"][0]["status"] == "closing"
    ctx.dry_run = True
    modes.set_account_mode(conn, accounts[0], "auto")
    _advance(ctx, 5)
    engine.tick(ctx, market="KR", today=D)
    assert b.cancelled == [] and _sells(conn) == []


def test_external_manual_sell_is_not_adopted(scalp_ctx, market, brokers, conn, project):
    """같은 종목 일반 보유분을 앱에서 판 외부 체결(mode='external')은 청산으로 흡수하지 않는다."""
    ctx = _ctx(scalp_ctx, market)
    b = _held_closing(ctx, project, brokers, market)
    _advance(ctx, 2)
    engine.tick(ctx, market="KR", today=D)  # closing 시작 + BUY 취소 + 20주 SELL
    xledger.insert_order(
        conn,
        decision_id=None,
        account_id="paper-main",
        ticker=T,
        market="KR",
        side="SELL",
        qty=100,
        order_type="MARKET",
        price=None,
        status="filled",
        order_no="EXT-1",
        message="",
        mode="external",
        created_at=ctx.now().isoformat(),
        origin="external",
    )
    _advance(ctx, 3)
    engine.tick(ctx, market="KR", today=D)
    leg = _st(project)["open"][0]["legs"]["paper-main"]
    ext = conn.execute("SELECT id FROM orders WHERE order_no='EXT-1'").fetchone()["id"]
    assert ext not in leg["sell"]
    assert b.cancelled == ["FAKE-1"]


def test_finished_position_leaves_open_before_next_persist(
    scalp_ctx, market, brokers, project, monkeypatch
):
    """포지션 둘: 앞의 것이 끝난 뒤 뒤의 것이 저장해도 앞의 것이 open·closed 양쪽에 남지 않는다."""
    ctx = _ctx(scalp_ctx, market)
    _enter(ctx, project)
    b = brokers["paper-main"]
    b.fills[("KR", D)] = [_fill("FAKE-1", "BUY", 49, "10050")]
    b.positions = [_pos(49)]
    market.prices[T] = Decimal("9900")
    _advance(ctx, 3)
    engine.tick(ctx, market="KR", today=D)  # A: 청산 주문 접수
    b.fills[("KR", D)].append(_fill("FAKE-2", "SELL", 49, "9900", minutes=3))
    st = _st(project)
    other = {
        "ticker": "000660",
        "exchange": None,
        "weight": 0.05,
        "entry_price": "10000",
        "entry_time": "090500",
        "entry_at": NOW.isoformat(),
        "qty": {"paper-main": 5},
        "legs": {"paper-main": {"req": 5, "dry": True, "buy": [], "sell": []}},
        "status": "held",
        "dry_run": True,
    }
    st["open"].append(other)  # B: 이번 틱에 청산을 시작한다(시작 상태를 중간 저장)
    save_state(project / "state", D, "KR", st)
    market.prices["000660"] = Decimal("9000")
    seen = []
    orig = scalp_state.save_state

    def spy(state, today, market, data):
        seen.append(([p["ticker"] for p in data["open"]], [c["ticker"] for c in data["closed"]]))
        return orig(state, today, market, data)

    # 옛 `engine.save_state` 하나 → 엔진·청산 모듈이 각각 이름으로 가져간 save_state 둘 다
    monkeypatch.setattr(engine, "save_state", spy)
    monkeypatch.setattr(exits, "save_state", spy)
    _advance(ctx, 4)
    out = engine.tick(ctx, market="KR", today=D)
    assert out["exits"][0]["ticker"] == T
    assert any("000660" in o for o, _ in seen)  # B 의 중간 저장이 있었고
    assert all(not (set(o) & set(c)) for o, c in seen)  # A 는 양쪽에 동시에 없다


def test_all_switches_off_drops_pending_but_exits_still_run(
    scalp_ctx, market, brokers, conn, project
):
    """켜진 계좌가 없으면 판정 대기는 버려지고(그날 종목 끝) 주문이 없다. 청산은 나간다."""
    from trader.features.scalp import switch

    ctx = _ctx(scalp_ctx, market)
    _enter(ctx, project)
    b = brokers["paper-main"]
    b.fills[("KR", D)] = [_fill("FAKE-1", "BUY", 49, "10000")]
    b.positions = [_pos(49)]
    st = _st(project)
    st["pending"] = {
        "000660": {"signal_price": "10050", "signal_time": "102900", "reason": "r",
                   "exchange": None, "submitted_at": NOW.isoformat()}
    }  # fmt: skip
    save_state(project / "state", D, "KR", st)
    buys = conn.execute("SELECT COUNT(*) FROM orders WHERE side='BUY'").fetchone()[0]
    for acc in ("paper-main", "paper-two"):
        set_flag(conn, switch.flag_key(acc), False)
    market.prices[T] = Decimal("9800")  # 손절 −1% 아래
    _advance(ctx, 3)
    out = engine.tick(ctx, market="KR", today=D)
    assert "켜진 계좌 없음" in (out.get("skipped") or out["entries_blocked"])
    after = _st(project)
    assert after["pending"] == {} and "000660" in after["done"]
    assert conn.execute("SELECT COUNT(*) FROM orders WHERE side='BUY'").fetchone()[0] == buys
    assert [r["qty"] for r in _sells(conn)] == [49]  # 청산은 스위치와 무관하게 나간다
