"""spot executor: 계좌별 판정 → 멱등 claim → 승인 요청/주문(코어) 테스트.

옛 `tests/orchestrator/test_executor.py` 에서 이식(task-11-test-map 의 51건). 치환: 계좌 모드·
일시정지는 accounts 기능, `repo.X` → `execution.ledger.X`·`spot.ledger.X`, `exec_keys` →
`exec_locks`·`buy_reservations`. 메모리 브레이커(M8)·RL(M7)·손절 origin(M5)·국면/킬스위치(M6)
테스트는 이연."""

import threading
from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.features.spot.conftest import PRICE, T0, D, make_account
from tests.features.spot.helpers import ok_decision
from trader.features.accounts import api as accounts_api
from trader.features.accounts import modes
from trader.features.accounts.settings import to_broker_account
from trader.features.spot import executor
from trader.features.spot import ledger as spot_ledger
from trader.features.spot.rules import RiskVerdict
from trader.features.spot.schemas import Decision
from trader.kernel.broker.errors import UNKNOWN_RESPONSE_CODE, KisApiError
from trader.kernel.broker.fake import FakeBroker
from trader.kernel.broker.models import Cash, Position
from trader.kernel.execution import core
from trader.kernel.execution import ledger as repo
from trader.kernel.ledger import control, db
from trader.kernel.session.run_store import read_checkpoint, run_dir


class FakeNotifier:
    def __init__(self):
        self.sent: list[str] = []

    def send(self, text: str, *, escape: bool = True) -> bool:
        self.sent.append(text)
        return True


def _accounts():
    return [
        make_account("paper-main", "paper", "auto", markets=["KR", "US"]),
        make_account("real-main", "real", "off", markets=["KR"]),
    ]


def _broker(a, **kw):
    return FakeBroker(
        to_broker_account(a),
        cash={
            "KR": Cash(currency="KRW", available=Decimal("10000000"), total=Decimal("10000000")),
            "US": Cash(currency="USD", available=Decimal("10000"), total=Decimal("10000")),
        },
        **kw,
    )


def _ctx(settings, conn, accounts, brokers, **kw):
    return executor.ExecContext(
        settings=settings,
        accounts=accounts,
        conn=conn,
        brokers=brokers,
        policy=accounts_api.effective_mode,
        paused=accounts_api.is_paused,
        notifier=kw.pop("notifier", None) or FakeNotifier(),
        now=lambda: T0,
        **kw,
    )


@pytest.fixture
def ectx(project, conn, settings):
    accounts = _accounts()
    brokers = {a.id: _broker(a) for a in accounts}
    return _ctx(settings, conn, accounts, brokers)


def _ingest(conn, dec: Decision, market="KR"):
    spot_ledger.ingest_checkpoint(
        conn, D, market, dec.ticker, "30_decision", {"data": dec.model_dump()}
    )
    return spot_ledger.decision_id(conn, D, market, dec.ticker)


def _run(ectx, decision=None, *, dry_run=False, market="KR", exchange=None, price=PRICE):
    dec = Decision(**(decision or ok_decision()))
    _ingest(ectx.conn, dec, market)
    return executor.execute(
        ectx, dec, date=D, market=market, price=price, exchange=exchange, dry_run=dry_run
    )


def _exchanges(conn, market):
    rows = conn.execute(
        "SELECT ticker, exchange FROM ticker_exchanges WHERE market=?", (market,)
    ).fetchall()
    return {r["ticker"]: r["exchange"] for r in rows}


def test_paper_auto_places_order_and_writes_checkpoints(ectx):
    results = _run(ectx)
    by = {r["account_id"]: r for r in results}
    assert by["paper-main"]["approved"] and by["paper-main"]["qty"] == 14  # 100만/지정가 71000
    orders = ectx.brokers["paper-main"].orders
    assert len(orders) == 1 and orders[0].qty == 14 and orders[0].price == Decimal("71000")
    rc = read_checkpoint(run_dir(D, "KR", "005930"), "40_risk_check_paper-main")
    assert rc["data"]["approved"] is True and rc["data"]["mode"] == "auto"
    oc = read_checkpoint(run_dir(D, "KR", "005930"), "50_order_paper-main")
    assert oc["data"]["result"]["accepted"] and oc["data"]["request"]["qty"] == 14
    row = ectx.conn.execute("SELECT status, order_no, mode FROM orders").fetchone()
    assert (row["status"], row["order_no"], row["mode"]) == ("accepted", "FAKE-1", "auto")
    assert ectx.conn.execute("SELECT COUNT(*) FROM risk_checks").fetchone()[0] == 2
    assert any("주문" in m and "paper-main" in m for m in ectx.notifier.sent)
    # v5: 빈 계좌의 NAV 스냅샷은 positions_snapshot(_CASH 행)이 아니라 account_nav 에 남는다
    assert ectx.conn.execute("SELECT COUNT(*) FROM account_nav").fetchone()[0] >= 1


def test_real_off_records_only(ectx):
    results = _run(ectx)
    real = next(r for r in results if r["account_id"] == "real-main")
    assert real["mode"] == "off" and real["order"] is None
    rc = read_checkpoint(run_dir(D, "KR", "005930"), "40_risk_check_real-main")
    assert rc["data"]["mode"] == "off" and isinstance(rc["data"]["approved"], bool)
    assert read_checkpoint(run_dir(D, "KR", "005930"), "50_order_real-main") is None
    assert ectx.brokers["real-main"].orders == []
    rows = ectx.conn.execute("SELECT account_id FROM orders").fetchall()
    assert all(r["account_id"] != "real-main" for r in rows)


def test_real_auto_requires_control_confirmation_record(ectx):
    real = ectx.accounts[1]
    ectx.conn.execute(  # 확인 문자열 없이 직접 쓴 값
        "INSERT INTO account_modes(account_id, mode, updated_at) VALUES ('real-main', 'auto', ?)",
        (T0.isoformat(),),
    )
    ectx.conn.commit()
    results = _run(ectx)
    r = next(x for x in results if x["account_id"] == "real-main")
    assert r["mode"] == "off" and "확인" in r["note"] and ectx.brokers["real-main"].orders == []
    modes.set_account_mode(ectx.conn, real, "auto", confirm="LIVE")
    assert modes.live_confirmed(ectx.conn, "real-main")
    results = _run(ectx, ok_decision(ticker="000660"))
    r = next(x for x in results if x["account_id"] == "real-main")
    assert r["mode"] == "auto" and len(ectx.brokers["real-main"].orders) == 1
    modes.set_account_mode(ectx.conn, real, "off")  # auto 해제 시 확인 기록도 지운다
    assert not modes.live_confirmed(ectx.conn, "real-main")


def test_approve_mode_creates_approval_and_notifies(ectx):
    modes.set_account_mode(ectx.conn, ectx.accounts[0], "approve")
    results = _run(ectx)
    r = next(x for x in results if x["account_id"] == "paper-main")
    assert r["mode"] == "approve" and r["approval_id"] is not None and r["order"] is None
    pending = repo.pending_approvals(ectx.conn)
    assert len(pending) == 1 and pending[0]["order"]["qty"] == 14
    assert pending[0]["expires_at"] == "2026-10-09T01:30:00+00:00"
    assert ectx.brokers["paper-main"].orders == []
    assert any("/approve" in m for m in ectx.notifier.sent)


def test_dry_run_no_orders(ectx):
    results = _run(ectx, dry_run=True)
    r = next(x for x in results if x["account_id"] == "paper-main")
    assert r["approved"] and r["dry_run"] and r["order"] is None
    assert ectx.brokers["paper-main"].orders == [] and repo.pending_approvals(ectx.conn) == []
    rc = read_checkpoint(run_dir(D, "KR", "005930"), "40_risk_check_paper-main")
    assert rc["data"]["dry_run"] is True
    assert read_checkpoint(run_dir(D, "KR", "005930"), "50_order_paper-main") is None


def test_broker_failure_recorded_and_continues(ectx):
    second = make_account("paper-2", "paper", "auto")
    ectx.accounts.append(second)
    ectx.brokers["paper-main"] = _broker(ectx.accounts[0], fail={"place_order"})
    ectx.brokers["paper-2"] = _broker(second)
    results = _run(ectx)
    by = {r["account_id"]: r for r in results}
    assert (
        by["paper-main"]["order"]["accepted"] is False
        and "가짜 실패" in by["paper-main"]["order"]["message"]
    )
    assert by["paper-2"]["order"]["accepted"] is True
    oc = read_checkpoint(run_dir(D, "KR", "005930"), "50_order_paper-main")
    assert oc["data"]["result"]["accepted"] is False
    rows = {r["account_id"]: r["status"] for r in ectx.conn.execute("SELECT * FROM orders")}
    assert rows == {"paper-main": "failed", "paper-2": "accepted"}


def test_balance_failure_rejects_account(ectx):
    ectx.brokers["paper-main"] = _broker(ectx.accounts[0], fail={"get_cash"})
    results = _run(ectx)
    r = next(x for x in results if x["account_id"] == "paper-main")
    assert r["approved"] is False and any("잔고" in s for s in r["reasons"])
    assert ectx.brokers["paper-main"].orders == []


def test_missing_broker_in_auto_is_rejected(ectx):
    """브로커가 없으면 잔고를 알 수 없으므로 판정 자체가 거부로 기록된다 (주문 없음)."""
    del ectx.brokers["paper-main"]
    results = _run(ectx)
    r = next(x for x in results if x["account_id"] == "paper-main")
    assert r["approved"] is False and r["order"] is None
    assert any("브로커" in s for s in r["reasons"])


def test_today_pnl_from_previous_snapshot_blocks_buy(ectx):
    repo.snapshot_positions(
        ectx.conn,
        account_id="paper-main",
        d=date(2026, 10, 8),
        market="KR",
        nav=Decimal("10800000"),
        cash=Decimal("10800000"),
        positions=[],
    )
    assert repo.latest_nav_before(ectx.conn, "paper-main", D, market="KR") == Decimal("10800000")
    results = _run(ectx)
    r = next(x for x in results if x["account_id"] == "paper-main")
    assert r["approved"] is False and any("일일 손실" in s for s in r["reasons"])


def test_us_order_carries_exchange(ectx):
    results = _run(
        ectx,
        ok_decision(ticker="AAPL", limit_price=200.0, take_profit=230.0, stop_loss=180.0),
        market="US",
        exchange="NAS",
        price=Decimal("200"),
    )
    r = next(x for x in results if x["account_id"] == "paper-main")
    assert r["approved"]
    o = ectx.brokers["paper-main"].orders[0]
    assert o.market == "US" and o.exchange == "NAS" and o.order_type == "LIMIT"


def _pending(ectx) -> int:
    modes.set_account_mode(ectx.conn, ectx.accounts[0], "approve")
    results = _run(ectx)
    return next(x for x in results if x["account_id"] == "paper-main")["approval_id"]


def test_approve_executes_order_once(ectx):
    aid = _pending(ectx)
    r = executor.approve(ectx, aid)
    assert r["ok"] and r["order"]["accepted"] and r["order"]["order_no"] == "FAKE-1"
    assert len(ectx.brokers["paper-main"].orders) == 1
    assert repo.get_approval(ectx.conn, aid)["status"] == "approved"
    oc = read_checkpoint(run_dir(D, "KR", "005930"), "50_order_paper-main")
    assert oc["data"]["mode"] == "approve" and oc["data"]["result"]["accepted"]
    row = ectx.conn.execute("SELECT mode, status FROM orders").fetchone()
    assert (row["mode"], row["status"]) == ("approve", "accepted")
    r2 = executor.approve(ectx, aid)  # 두 번째 승인은 거부, 주문 1건 유지
    assert r2["ok"] is False and len(ectx.brokers["paper-main"].orders) == 1
    assert executor.approve(ectx, 999)["ok"] is False


def test_expired_approval_not_executed(ectx):
    aid = _pending(ectx)
    ectx.now = lambda: T0 + timedelta(minutes=31)
    r = executor.approve(ectx, aid)
    assert r["ok"] is False and "만료" in r["error"]
    assert ectx.brokers["paper-main"].orders == []
    assert repo.get_approval(ectx.conn, aid)["status"] == "expired"


def test_reject_and_expire_pass(ectx):
    aid = _pending(ectx)
    assert executor.reject(ectx, aid)["ok"]
    assert repo.get_approval(ectx.conn, aid)["status"] == "rejected"
    assert executor.reject(ectx, aid)["ok"] is False
    aid2 = _pending(ectx)
    ectx.now = lambda: T0 + timedelta(hours=1)
    assert executor.expire_approvals(ectx) == [aid2]
    assert repo.pending_approvals(ectx.conn) == []


# --- 리뷰 반영 ---------------------------------------------------------------------


def _pos(**kw):
    base = dict(
        ticker="005930",
        market="KR",
        qty=10,
        avg_price=Decimal("70000"),
        current_price=Decimal("71500"),
        eval_amount=Decimal("715000"),
        pnl_pct=0.02,
    )
    base.update(kw)
    return Position(**base)


def test_resubmit_does_not_duplicate_order(ectx):
    """같은 결정을 두 번 실행해도(세션 재시도·재실행) 계좌당 주문은 1건."""
    ectx.brokers["paper-main"].positions = [_pos()]
    dec = ok_decision(action="SELL", target_weight=0.0)
    _run(ectx, dec)
    r = _run(ectx, dec)
    assert len(ectx.brokers["paper-main"].orders) == 1
    rr = next(x for x in r if x["account_id"] == "paper-main")
    assert rr["order"] is None and any("이미" in s for s in rr["reasons"])


def test_resubmit_does_not_duplicate_approval(ectx):
    modes.set_account_mode(ectx.conn, ectx.accounts[0], "approve")
    _run(ectx)
    _run(ectx)
    assert len(repo.pending_approvals(ectx.conn)) == 1


def test_place_value_error_recorded_and_continues(ectx):
    class BadBroker:
        def __init__(self, inner):
            self.inner = inner

        def get_positions(self, m):
            return self.inner.get_positions(m)

        def get_cash(self, m):
            return self.inner.get_cash(m)

        def place_order(self, req):
            raise ValueError("US 주문에는 exchange 가 필요합니다")

    second = make_account("paper-2", "paper", "auto")
    ectx.accounts.append(second)
    ectx.brokers["paper-2"] = _broker(second)
    ectx.brokers["paper-main"] = BadBroker(_broker(ectx.accounts[0]))
    results = _run(ectx)
    by = {r["account_id"]: r for r in results}
    assert by["paper-main"]["order"]["accepted"] is False
    assert by["paper-2"]["order"]["accepted"] is True
    assert read_checkpoint(run_dir(D, "KR", "005930"), "50_order_paper-main") is not None


def test_approve_failure_marks_status_failed_not_lost(ectx):
    aid = _pending(ectx)
    ectx.brokers["paper-main"] = _broker(ectx.accounts[0], fail={"place_order"})
    r = executor.approve(ectx, aid)
    assert r["ok"] is False
    assert repo.get_approval(ectx.conn, aid)["status"] == "failed"
    assert (
        read_checkpoint(run_dir(D, "KR", "005930"), "50_order_paper-main")["data"]["status"]
        == "failed"
    )


def test_approve_conditional_update_guards_race(ectx):
    aid = _pending(ectx)
    # 다른 프로세스가 먼저 처리한 상황을 흉내: pending 이 아니면 UPDATE 가 0행
    assert repo.decide_approval(ectx.conn, aid, "approved") is True
    assert repo.decide_approval(ectx.conn, aid, "approved") is False
    assert executor.approve(ectx, aid)["ok"] is False
    assert ectx.brokers["paper-main"].orders == []


def test_dual_market_aggregates_are_per_market(ectx):
    """KR 1천만원 스냅샷·주문이 US(1만달러) 판정에 섞이면 안 된다."""
    _run(ectx)  # KR BUY 14주 @71000 = 994,000 KRW 주문 + KR 스냅샷
    assert repo.today_order_amount(ectx.conn, "paper-main", D, market="US") == Decimal(0)
    assert repo.today_order_amount(ectx.conn, "paper-main", D, market="KR") == Decimal("994000")
    r = _run(
        ectx,
        ok_decision(ticker="AAPL", limit_price=200.0, take_profit=230.0, stop_loss=180.0),
        market="US",
        exchange="NAS",
        price=Decimal("200"),
    )
    us = next(x for x in r if x["account_id"] == "paper-main")
    assert us["approved"] and us["qty"] == 5  # 0.1 × 10,000 / 200
    # 다음 날 US 일일 손익은 US NAV 기준 (KR nav 1천만원이 아니라 1만달러)
    assert repo.latest_nav_before(
        ectx.conn, "paper-main", D + timedelta(days=1), market="US"
    ) == Decimal("10000")
    assert repo.latest_nav_before(
        ectx.conn, "paper-main", D + timedelta(days=1), market="KR"
    ) == Decimal("10000000")


def test_risk_check_checkpoint_round_trips_as_verdict(ectx):
    _run(ectx)
    cp = read_checkpoint(run_dir(D, "KR", "005930"), "40_risk_check_paper-main")
    v = RiskVerdict.model_validate(cp["data"])
    assert v.mode == "auto" and v.dry_run is False and v.stance == "normal"


# --- 일시정지: paused 면 주문·승인 요청·승인 실행을 내지 않는다 ---


def test_paused_blocks_orders_and_approval_requests(ectx):
    control.set_flag(ectx.conn, "paused", True)
    r = _run(ectx)
    p = next(x for x in r if x["account_id"] == "paper-main")
    assert p["order"] is None and p["approval_id"] is None
    assert p["approved"] is True and "일시정지" in (p["note"] or "")
    assert ectx.brokers["paper-main"].orders == [] and repo.pending_approvals(ectx.conn) == []


def _paused_run(ectx, decision, **kw):
    control.set_flag(ectx.conn, "paused", True)
    ectx.brokers["paper-main"].positions = [_pos()]
    dec = Decision(**decision)
    _ingest(ectx.conn, dec)
    return executor.execute(ectx, dec, date=D, market="KR", price=PRICE, dry_run=False, **kw)


def test_paused_stop_loss_sell_transmits(ectx):
    r = _paused_run(ectx, ok_decision(action="SELL", target_weight=0.0), is_stop_loss=True)
    p = next(x for x in r if x["account_id"] == "paper-main")
    assert p["order"] is not None and "일시정지" not in (p["note"] or "")
    assert len(ectx.brokers["paper-main"].orders) == 1


def test_paused_scalp_exit_sell_transmits(ectx):
    _paused_run(ectx, ok_decision(action="SELL", target_weight=0.0), scalp_leg="exit")
    assert len(ectx.brokers["paper-main"].orders) == 1


def test_paused_scalp_buy_blocked(ectx):
    _paused_run(ectx, ok_decision(), scalp_leg="entry")
    assert ectx.brokers["paper-main"].orders == []


def test_paused_analyze_sell_blocked(ectx):
    r = _paused_run(ectx, ok_decision(action="SELL", target_weight=0.0))
    p = next(x for x in r if x["account_id"] == "paper-main")
    assert p["order"] is None and "일시정지" in (p["note"] or "")
    assert ectx.brokers["paper-main"].orders == []


def test_paused_scalp_exit_approve_mode_creates_no_request(ectx):
    modes.set_account_mode(ectx.conn, ectx.accounts[0], "approve")
    r = _paused_run(ectx, ok_decision(action="SELL", target_weight=0.0), scalp_leg="exit")
    p = next(x for x in r if x["account_id"] == "paper-main")
    assert p["order"] is None and p["approval_id"] is None and "일시정지" in (p["note"] or "")
    assert ectx.brokers["paper-main"].orders == [] and repo.pending_approvals(ectx.conn) == []


def test_paused_stop_loss_off_mode_no_order(ectx):
    modes.set_account_mode(ectx.conn, ectx.accounts[0], "off")
    _paused_run(ectx, ok_decision(action="SELL", target_weight=0.0), is_stop_loss=True)
    assert ectx.brokers["paper-main"].orders == []


def test_paused_stop_loss_dry_run_no_order(ectx):
    control.set_flag(ectx.conn, "paused", True)
    ectx.brokers["paper-main"].positions = [_pos()]
    dec = Decision(**ok_decision(action="SELL", target_weight=0.0))
    _ingest(ectx.conn, dec)
    executor.execute(ectx, dec, date=D, market="KR", price=PRICE, dry_run=True, is_stop_loss=True)
    assert ectx.brokers["paper-main"].orders == []


def test_paused_blocks_approve(ectx):
    aid = _pending(ectx)
    control.set_flag(ectx.conn, "paused", True)
    r = executor.approve(ectx, aid)
    assert r["ok"] is False and "일시정지" in r["error"]
    assert repo.get_approval(ectx.conn, aid)["status"] == "pending"  # 요청은 살아 있다
    control.set_flag(ectx.conn, "paused", False)
    assert executor.approve(ectx, aid)["ok"]


def test_executor_passes_holiday_fn_to_rules(ectx):
    """보유 1거래일(10/8 매수) 인데 10/9 가 휴장이면 is_holiday 가 거래일 계산에 반영된다."""
    ectx.brokers["paper-main"].positions = [_pos()]
    dec_id = _ingest(ectx.conn, Decision(**ok_decision()))
    repo.insert_order(
        ectx.conn,
        decision_id=dec_id,
        account_id="paper-main",
        ticker="005930",
        market="KR",
        side="BUY",
        qty=10,
        order_type="LIMIT",
        price=Decimal("70000"),
        status="accepted",
        order_no="0",
        message="",
        mode="auto",
        created_at="2026-10-05T01:00:00+00:00",
    )
    # 10/5(월) 매수 → 10/9: 평일 4일 → 통과. 10/6·10/7·10/8 휴장이면 1일 → 거부
    closed = {date(2026, 10, 6), date(2026, 10, 7), date(2026, 10, 8)}
    ectx.holiday_fn = lambda _m, d: d in closed  # (시장, 날짜)
    r = _run(ectx, ok_decision(action="SELL", target_weight=0.0))
    p = next(x for x in r if x["account_id"] == "paper-main")
    assert p["approved"] is False and any("보유" in s for s in p["reasons"])


def test_build_portfolio_records_us_exchange_from_balance(ectx):
    """잔고 응답의 US 거래소를 원장 `ticker_exchanges` 에 남긴다 — 유니버스 밖 보유·미만기 종목의
    일일 스냅샷이 거래소를 알 수 있게. KR 은 적지 않는다."""
    acct = ectx.accounts[0]
    broker = ectx.brokers[acct.id]
    broker.positions = [
        _pos(
            ticker="RIVN",
            market="US",
            qty=3,
            avg_price=Decimal("10"),
            current_price=Decimal("11"),
            eval_amount=Decimal("33"),
            pnl_pct=0.1,
            exchange="NAS",
        ),
        _pos(qty=1, eval_amount=Decimal("71500")),
    ]
    executor.build_portfolio(ectx.conn, broker, acct, "US", D)
    executor.build_portfolio(ectx.conn, broker, acct, "KR", D)
    assert _exchanges(ectx.conn, "US") == {"RIVN": "NAS"}
    assert _exchanges(ectx.conn, "KR") == {}


def test_approved_market_order_records_amount_for_cash_deduction(ectx):
    """승인 뒤 낸 KR 시장가 주문도 amount(요청 시 현재가 × 수량)를 남겨 가능금액 차감에 들어간다."""
    modes.set_account_mode(ectx.conn, ectx.accounts[0], "approve")
    r = _run(ectx, ok_decision(order_type="MARKET", limit_price=None))
    aid = next(x for x in r if x["account_id"] == "paper-main")["approval_id"]
    assert executor.approve(ectx, aid)["ok"]
    row = ectx.conn.execute("SELECT amount, price FROM orders").fetchone()
    assert row["price"] is None and row["amount"] == str(Decimal(13) * PRICE)
    assert repo.today_order_amount(ectx.conn, "paper-main", D, market="KR") == Decimal(13) * PRICE


class RaisingNotifier(FakeNotifier):
    def send(self, text: str, *, escape: bool = True) -> bool:
        super().send(text)
        raise RuntimeError("텔레그램 다운")


def test_place_keeps_ledger_row_when_notify_fails(ectx):
    """주문이 접수된 뒤 알림이 실패해도 orders 행·50_order 는 남고 결과가 돌아와야 한다
    (행이 없으면 재실행 때 has_open_order_or_approval 이 비어 같은 주문을 다시 낸다)."""
    ectx.notifier = RaisingNotifier()
    results = _run(ectx)
    r = next(x for x in results if x["account_id"] == "paper-main")
    assert r["order"]["accepted"] and len(ectx.brokers["paper-main"].orders) == 1
    n = ectx.conn.execute("SELECT COUNT(*) AS n FROM orders WHERE status='accepted'").fetchone()
    assert n["n"] == 1
    assert read_checkpoint(run_dir(D, "KR", "005930"), "50_order_paper-main") is not None


def test_place_insert_failure_is_critical_and_notified(ectx, monkeypatch):
    """접수 뒤 insert_order 가 실패하면 접수번호를 알림에 남기고 예외를 올린다."""

    def boom(*a, **kw):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(core, "insert_order", boom)
    with pytest.raises(RuntimeError):
        _run(ectx)
    assert any("FAKE-1" in s and "기록 실패" in s for s in ectx.notifier.sent)


def test_approve_bookkeeping_failure_marks_failed_not_stuck(ectx, monkeypatch):
    """승인 뒤 주문 기록이 예외로 끊기면 승인이 approved 로 굳지 않고 failed 가 돼야 한다."""
    aid = _pending(ectx)

    def boom(*a, **kw):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(core, "insert_order", boom)
    r = executor.approve(ectx, aid)
    assert r["ok"] is False and "FAKE-1" in r["error"]
    assert repo.get_approval(ectx.conn, aid)["status"] == "failed"


def test_unknown_network_result_is_recorded_as_unknown_and_counts_as_live(ectx):
    """응답을 못 받은 주문(NETWORK_UNKNOWN)은 failed 가 아니라 unknown 으로 남겨
    중복 방지 조건이 살아 있는 주문으로 보게 한다 (KIS 에 접수됐을 수 있다)."""

    class TimeoutBroker(FakeBroker):
        def place_order(self, request):
            raise KisApiError("NETWORK_UNKNOWN", "요청 실패: ReadTimeout", "TTTC0802U")

    cash = _broker(ectx.accounts[0]).cash
    ectx.brokers["paper-main"] = TimeoutBroker(to_broker_account(ectx.accounts[0]), cash=cash)
    results = _run(ectx)
    r = next(x for x in results if x["account_id"] == "paper-main")
    assert r["order"]["status"] == "unknown" and r["order"]["accepted"] is False
    row = ectx.conn.execute("SELECT status, order_no FROM orders").fetchone()
    assert (row["status"], row["order_no"]) == ("unknown", None)
    dec_id = spot_ledger.decision_id(ectx.conn, D, "KR", "005930")
    assert repo.has_open_order_or_approval(ectx.conn, dec_id, "paper-main", "BUY")
    assert repo.open_orders_today(ectx.conn, "paper-main", "005930", "BUY", D, market="KR")
    assert "005930" in repo.today_buy_tickers(ectx.conn, "paper-main", D, market="KR")
    assert any("미확인" in m for m in ectx.notifier.sent)
    # "살아 있을 수 있다" 고 알린 주문을 뒤이어 "[주문 실패]" 로 또 알리면 운영자가 재주문한다
    assert not any("주문 실패" in m for m in ectx.notifier.sent)


def test_approve_rejected_when_account_mode_is_no_longer_approve(ectx):
    """요청 뒤 계좌를 off 로 내렸으면 승인해도 주문이 나가지 않는다 (요청은 pending 유지)."""
    aid = _pending(ectx)
    modes.set_account_mode(ectx.conn, ectx.accounts[0], "off")
    r = executor.approve(ectx, aid)
    assert r["ok"] is False and "모드" in r["error"]
    assert ectx.brokers["paper-main"].orders == []
    assert repo.get_approval(ectx.conn, aid)["status"] == "pending"


def test_rejected_order_bookkeeping_failure_is_not_escalated(ectx, monkeypatch):
    """브로커가 거부한 주문(접수번호 없음)의 기록 실패는 긴급 알림·중단 대상이 아니다."""
    second = make_account("paper-2", "paper", "auto")
    ectx.accounts.append(second)
    ectx.brokers["paper-main"] = _broker(ectx.accounts[0], fail={"place_order"})
    ectx.brokers["paper-2"] = _broker(second)
    real_insert = core.insert_order
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database is locked")
        return real_insert(*a, **kw)

    monkeypatch.setattr(core, "insert_order", flaky)
    results = _run(ectx)
    by = {r["account_id"]: r for r in results}
    assert by["paper-main"]["order"]["status"] == "failed"
    assert by["paper-2"]["order"]["accepted"] is True  # 다음 계좌는 계속 실행된다
    assert not any("긴급" in m for m in ectx.notifier.sent)


# --- 리뷰 F8·F9·F17 -------------------------------------------------------------------


def test_account_exception_does_not_block_other_accounts(ectx, monkeypatch):
    """한 계좌의 예외(원장 잠금 등)는 그 계좌의 error 항목으로 남고 다음 계좌는 계속 실행된다."""
    ectx.accounts[1] = make_account("real-main", "real", "off")
    orig = executor.build_portfolio

    def boom(conn, broker, account, *a, **kw):
        if account.id == "paper-main":
            raise OSError("disk")
        return orig(conn, broker, account, *a, **kw)

    monkeypatch.setattr(executor, "build_portfolio", boom)
    results = _run(ectx)
    by = {r["account_id"]: r for r in results}
    assert "OSError" in by["paper-main"]["error"]
    assert by["real-main"]["mode"] == "off" and "error" not in by["real-main"]
    assert read_checkpoint(run_dir(D, "KR", "005930"), "40_risk_check_real-main") is not None
    assert any("[실행 실패]" in m for m in ectx.notifier.sent)


def test_skip_accounts_only_runs_remaining(ectx):
    results = executor.execute(
        ectx,
        Decision(**ok_decision()),
        date=D,
        market="KR",
        price=PRICE,
        dry_run=True,
        skip_accounts=frozenset({"paper-main"}),
    )
    assert [r["account_id"] for r in results] == ["real-main"]


def test_pending_approval_reserves_cash(ectx):
    """approve 계좌의 pending 요청 금액은 당일 주문금액에 들어가 다음 매수의 가능금액을 줄인다."""
    aid = _pending(ectx)
    a = repo.get_approval(ectx.conn, aid)
    qty, price = int(a["order"]["qty"]), Decimal(str(a["order"]["price"] or PRICE))
    assert repo.today_order_amount(ectx.conn, "paper-main", D, market="KR") == qty * price
    executor.reject(ectx, aid)
    assert repo.today_order_amount(ectx.conn, "paper-main", D, market="KR") == 0


def test_approval_keeps_order_stage_and_unknown_is_not_failed(ectx):
    """재평가 승인은 51_order_r<n> 에 쓰고(오늘 분석의 50_order 를 덮지 않음), 응답 없는 주문은
    failed 가 아니라 approved 로 두어 체결 동기화가 확정하게 한다."""
    modes.set_account_mode(ectx.conn, ectx.accounts[0], "approve")
    dec = Decision(**ok_decision())
    _ingest(ectx.conn, dec)
    results = executor.execute(
        ectx, dec, date=D, market="KR", price=PRICE, dry_run=False, resume_round=1
    )
    aid = next(x for x in results if x["account_id"] == "paper-main")["approval_id"]
    assert repo.get_approval(ectx.conn, aid)["order"]["order_stage"] == "51_order_r1"

    class TimeoutBroker(FakeBroker):
        def place_order(self, request):
            raise KisApiError("NETWORK_UNKNOWN", "요청 실패: ReadTimeout", "TTTC0802U")

    ectx.brokers["paper-main"] = TimeoutBroker(
        to_broker_account(ectx.accounts[0]), cash=_broker(ectx.accounts[0]).cash
    )
    r = executor.approve(ectx, aid)
    assert r["ok"] is False and r["order"]["status"] == "unknown"
    assert repo.get_approval(ectx.conn, aid)["status"] == "approved"
    d = run_dir(D, "KR", "005930")
    assert read_checkpoint(d, "51_order_r1_paper-main") is not None
    assert read_checkpoint(d, "50_order_paper-main") is None


def test_reject_race_is_reported(ectx):
    aid = _pending(ectx)
    repo.decide_approval(ectx.conn, aid, "rejected")  # 다른 프로세스가 먼저 처리
    r = executor.reject(ectx, aid)
    assert r["ok"] is False and "이미" in r["error"]


# --- 실행 키 선점 -----------------------------------------------------------------------


def test_concurrent_execute_places_one_order(ectx, monkeypatch):
    """두 프로세스(스레드 + 각자 연결)가 같은 결정을 동시에 실행해도 주문은 1건."""
    paper = ectx.accounts[0]
    barrier = threading.Barrier(2, timeout=10)

    real_check = repo.has_open_order_or_approval

    def check_then_wait(*a, **kw):
        out = real_check(*a, **kw)
        barrier.wait()  # 둘 다 중복 확인을 "없음" 으로 지난 뒤에 주문으로 간다
        return out

    monkeypatch.setattr(repo, "has_open_order_or_approval", check_then_wait)
    broker = _broker(paper)
    dec = Decision(**ok_decision())
    _ingest(ectx.conn, dec)
    results: list = []

    def worker():
        c = db.connect()
        try:
            ctx = _ctx(ectx.settings, c, [paper], {paper.id: broker})
            results.extend(
                executor.execute(ctx, dec, date=D, market="KR", price=PRICE, dry_run=False)
            )
        finally:
            c.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(broker.orders) == 1
    assert sorted(r["approved"] for r in results) == [False, True]
    lost = next(r for r in results if not r["approved"])
    assert any("다른 프로세스" in s for s in lost["reasons"])
    row = ectx.conn.execute("SELECT key, order_id FROM exec_locks").fetchone()
    assert row["order_id"] is not None and row["key"].endswith("|paper-main|BUY|50_order")


def test_rejected_order_releases_exec_key(ectx):
    calls = []

    def reject_once(request):
        calls.append(request)
        if len(calls) == 1:
            raise KisApiError("APBK0013", "주문 거부")
        return FakeBroker.place_order(ectx.brokers["paper-main"], request)

    ectx.brokers["paper-main"].place_order = reject_once
    _run(ectx)
    assert ectx.conn.execute("SELECT COUNT(*) FROM exec_locks").fetchone()[0] == 0
    r = next(x for x in _run(ectx) if x["account_id"] == "paper-main")
    assert r["order"]["accepted"] and len(calls) == 2
    assert ectx.conn.execute("SELECT COUNT(*) FROM exec_locks").fetchone()[0] == 1


def test_unknown_order_keeps_exec_key(ectx):
    def boom(request):
        raise KisApiError(UNKNOWN_RESPONSE_CODE, "응답 없음")

    ectx.brokers["paper-main"].place_order = boom
    _run(ectx)
    assert ectx.conn.execute("SELECT COUNT(*) FROM exec_locks").fetchone()[0] == 1


def test_approve_buy_expires_when_daily_loss_limit_hit(ectx):
    aid = _pending(ectx)
    repo.snapshot_positions(
        ectx.conn,
        account_id="paper-main",
        d=date(2026, 10, 8),
        market="KR",
        nav=Decimal("10800000"),  # 오늘 1000만 → −7.4%
        cash=Decimal("10800000"),
        positions=[],
    )
    r = executor.approve(ectx, aid)
    assert r["ok"] is False and "일일 손실" in r["error"]
    assert ectx.brokers["paper-main"].orders == []
    assert repo.get_approval(ectx.conn, aid)["status"] == "expired"


def _keys(ectx):
    return [dict(r) for r in ectx.conn.execute("SELECT * FROM exec_locks ORDER BY key")]


def test_approval_request_claims_key_and_approve_binds_order(ectx):
    """승인 모드도 요청 때 키를 잡는다(두 프로세스가 같은 결정의 승인 요청을 둘 만들지 않게)."""
    aid = _pending(ectx)
    (k,) = _keys(ectx)
    assert k["approval_id"] == aid and k["sent_at"] is None
    assert executor.approve(ectx, aid)["ok"]
    (k,) = _keys(ectx)
    assert k["order_id"] is not None and k["sent_at"] is not None


def test_closed_approval_key_is_reclaimed(ectx):
    aid = _pending(ectx)
    executor.reject(ectx, aid)
    r = next(x for x in _run(ectx) if x["account_id"] == "paper-main")
    assert r["approval_id"] and r["approval_id"] != aid  # 거절 뒤 재제출은 새 요청


def test_pending_approval_key_blocks_other_process(ectx):
    """다른 프로세스가 잡은 살아 있는 승인 키가 있으면 승인 요청을 또 만들지 않는다."""
    aid = _pending(ectx)
    # 중복 확인·같은 종목 체결 전 비중 우회 — 실행 키만 남긴다
    ectx.conn.execute(
        "UPDATE approvals SET decision_id=0, "
        "order_json=json_set(order_json, '$.ticker', '000660') WHERE id=?",
        (aid,),
    )
    ectx.conn.commit()
    r = next(x for x in _run(ectx) if x["account_id"] == "paper-main")
    assert r["approval_id"] is None and any("실행 키" in s for s in r["reasons"])


def test_stale_unsent_key_is_reclaimed_but_sent_key_is_not(ectx):
    dec = Decision(**ok_decision())
    did = _ingest(ectx.conn, dec)
    key = f"{did}|paper-main|BUY|50_order"
    old = (T0 - timedelta(minutes=11)).isoformat()
    ectx.conn.execute(
        "INSERT INTO exec_locks(key, created_at, sent_at) VALUES (?, ?, ?)", (key, old, old)
    )
    ectx.conn.commit()
    executor.execute(ectx, dec, date=D, market="KR", price=PRICE, dry_run=False)
    assert ectx.brokers["paper-main"].orders == []  # 보냈는데 행이 없는 키: 수동 대조
    ectx.conn.execute("UPDATE exec_locks SET sent_at=NULL WHERE key=?", (key,))
    ectx.conn.commit()
    executor.execute(ectx, dec, date=D, market="KR", price=PRICE, dry_run=False)
    assert len(ectx.brokers["paper-main"].orders) == 1  # 선점 뒤 죽은 프로세스의 키는 넘겨받음


def test_decision_turned_away_from_buy_cancels_open_buy(ectx):
    _run(ectx)  # 아침 BUY 지정가 접수
    b = ectx.brokers["paper-main"]
    assert len(b.orders) == 1
    hold = ok_decision(action="HOLD", target_weight=0.0)
    _run(ectx, hold)
    assert b.cancelled == ["FAKE-1"]
    st = ectx.conn.execute("SELECT status FROM orders WHERE account_id='paper-main'").fetchone()
    assert st["status"] == "cancelled"
    assert any("미체결 매수 취소" in m for m in ectx.notifier.sent)


def test_dry_run_hold_does_not_cancel(ectx):
    _run(ectx)
    _run(ectx, ok_decision(action="HOLD", target_weight=0.0), dry_run=True)
    assert ectx.brokers["paper-main"].cancelled == []


def test_record_portfolio_false_skips_snapshot(ectx):
    """FAKE 세션(가짜 브로커 잔고)은 판정만 하고 positions_snapshot 을 건드리지 않는다 — 가짜 NAV
    가 성과 기준선을 오염시키지 않게. (킬스위치 기준 NAV 단언은 M6 — Ruling 15)"""
    ectx.record_portfolio = False
    r = _run(ectx)
    assert next(x for x in r if x["account_id"] == "paper-main")["approved"]
    # v5: NAV 스냅샷은 account_nav(옛 positions_snapshot 의 _CASH 행)
    n = ectx.conn.execute("SELECT COUNT(*) AS n FROM account_nav").fetchone()["n"]
    assert n == 0
    ectx.record_portfolio = True
    _run(ectx)
    n = ectx.conn.execute("SELECT COUNT(*) AS n FROM account_nav").fetchone()["n"]
    assert n > 0


def test_record_portfolio_stores_available_cash(ectx):
    """잔고 스냅샷은 예수금(`cash.total`)과 주문 가능 금액(`cash.available`)을 함께 남긴다."""
    _run(ectx)
    row = ectx.conn.execute(
        "SELECT cash, cash_available FROM account_nav WHERE market='KR' LIMIT 1"
    ).fetchone()
    assert row["cash_available"] == "10000000" and row["cash"] is not None


def _inflight(conn, *, ticker="000660", amount="9000000", created_at=None, key="other|k"):
    """다른 프로세스가 방금 선점했지만 아직 주문 행이 없는 매수 예약(판정 스냅샷에 안 보인다)."""
    conn.execute(
        "INSERT INTO exec_locks(key, created_at) VALUES (?, ?)",
        (key, (created_at or T0).isoformat()),
    )
    conn.execute(
        "INSERT INTO buy_reservations(key, account_id, market, ticker, side, amount) "
        "VALUES (?, 'paper-main', 'KR', ?, 'BUY', ?)",
        (key, ticker, amount),
    )
    conn.commit()


def test_inflight_reservation_shrinks_concurrent_buy(ectx):
    """다른 경로가 같은 계좌에서 막 선점한 매수 예약은 이 판정의 스냅샷에 없다 — 키를 잡는
    트랜잭션 안에서 진행 중 예약을 반영해 게이트를 다시 돌려, 현금이 모자라면 수량을 줄이고
    키와 예약을 함께 남긴다."""
    _inflight(ectx.conn, amount="9500000")
    by = {r["account_id"]: r for r in _run(ectx)}
    v = by["paper-main"]
    assert v["approved"] and 0 < v["qty"] < 14
    assert any("동시 판정" in x for x in v["reasons"])
    row = ectx.conn.execute(
        "SELECT account_id, market, ticker, side, amount FROM buy_reservations "
        "WHERE key != 'other|k'"
    ).fetchone()
    assert (row["account_id"], row["market"], row["ticker"], row["side"]) == (
        "paper-main",
        "KR",
        "005930",
        "BUY",
    )
    assert Decimal(row["amount"]) > 0


def test_inflight_reservation_can_reject_when_nothing_fits(ectx):
    """진행 중 예약을 반영해 게이트를 다시 보면 거부될 수 있다. (옛 테스트는 섹터 상한 40% 로
    9.9백만에서 거부를 냈다 — 섹터 맵은 M6 이라 현금만으로 1주도 안 남는 9.99백만을 쓴다)"""
    _inflight(ectx.conn, amount="9990000")
    by = {r["account_id"]: r for r in _run(ectx)}
    v = by["paper-main"]
    assert not v["approved"] and any("동시 판정" in x for x in v["reasons"])
    assert ectx.brokers["paper-main"].orders == []


def test_stale_unsent_reservation_is_ignored(ectx):
    """보내지도 묶이지도 않은 채 오래된 예약(선점 뒤 프로세스가 죽음)은 진행 중으로 보지 않는다."""
    _inflight(ectx.conn, created_at=T0 - timedelta(hours=1))
    by = {r["account_id"]: r for r in _run(ectx)}
    assert by["paper-main"]["qty"] == 14


def _bind(conn, oid, at):
    conn.execute("UPDATE exec_locks SET order_id=? WHERE key='other|k'", (oid,))
    conn.execute("UPDATE buy_reservations SET bound_at=? WHERE key='other|k'", (at,))
    conn.commit()


def test_reservation_visible_in_snapshot_is_not_double_counted(ectx):
    """판정 스냅샷 전에 주문 행이 묶인 예약은 이미 당일 주문금액에 들어 있다 — 다시 더하지
    않는다."""
    oid = repo.insert_order(
        ectx.conn,
        decision_id=None,
        account_id="paper-main",
        ticker="000660",
        market="KR",
        side="BUY",
        qty=10,
        order_type="LIMIT",
        price=Decimal("100000"),
        status="accepted",
        order_no="9",
        message="",
        mode="auto",
        created_at=(T0 - timedelta(minutes=1)).isoformat(),
    )
    _inflight(ectx.conn, amount="1000000", created_at=T0 - timedelta(minutes=1))
    _bind(ectx.conn, oid, (T0 - timedelta(minutes=1)).isoformat())
    by = {r["account_id"]: r for r in _run(ectx)}
    assert by["paper-main"]["qty"] == 14 and not any(
        "동시 판정" in x for x in by["paper-main"]["reasons"]
    )


def test_reservation_bound_during_balance_read_is_not_double_counted(ectx):
    """판정이 잔고를 읽는 동안 다른 경로가 주문 행을 묶으면(묶인 시각은 판정 시작 뒤) 그 주문은
    이미 포트폴리오의 당일 주문금액에 들어 있다 — 시각이 아니라 포트폴리오가 실제로 읽은 원장
    표시(최대 주문 id)로 가려 다시 더하지 않는다(두 번 세면 현금이 모자라 수량이 준다)."""
    oid = repo.insert_order(
        ectx.conn,
        decision_id=None,
        account_id="paper-main",
        ticker="000660",
        market="KR",
        side="BUY",
        qty=10,
        order_type="LIMIT",
        price=Decimal("460000"),
        status="accepted",
        order_no="9",
        message="",
        mode="auto",
        created_at=T0.isoformat(),
    )
    _inflight(ectx.conn, amount="4600000")
    _bind(ectx.conn, oid, (T0 + timedelta(minutes=1)).isoformat())
    by = {r["account_id"]: r for r in _run(ectx)}
    assert by["paper-main"]["qty"] == 14 and not any(
        "동시 판정" in x for x in by["paper-main"]["reasons"]
    )


# --- ORIGIN 표 -----------------------------------------------------------------------


def test_origin_table_matches_flags():
    """실행 종류 플래그 → OriginSpec(우선순위 stop > scalp > reprice > rebalance > hedge > park >
    resume > llm). 체크포인트 이름은 표의 값(재평가·단타는 접미사)."""
    o = executor.origin_of
    assert o().origin == "llm" and o().risk_stage == "40_risk_check"
    assert o(resume_round=2).risk_stage == "41_risk_check_r2"
    assert o(resume_round=2).order_stage == "51_order_r2" and o(resume_round=2).origin == "llm"
    assert o(is_stop_loss=True, is_hedge=True).origin == "stop"
    assert o(scalp_leg="entry").order_stage == "57_order_scalp_entry"
    assert o(scalp_leg="exit", is_reprice=True).origin == "scalp"
    assert o(is_reprice=True, is_rebalance=True).origin == "reprice"
    assert o(is_rebalance=True, is_hedge=True).origin == "rebalance"
    assert o(is_hedge=True, is_park=True).origin == "hedge"
    assert o(is_park=True).risk_stage == "48_park"
    assert not o(is_rebalance=True).has_decision_row and o(is_hedge=True).has_decision_row
    assert {x.origin for x in executor.ORIGINS} == {
        "llm", "stop", "hedge", "scalp", "rebalance", "reprice", "park",
    }  # fmt: skip


def test_plan_of_returns_gate_plan(ectx):
    """`plan_of` 는 판정을 `GatePlan` 으로 — 승인된 판정만 주문 의도 1건."""
    from trader.kernel.execution.types import GatePlan

    acct = ectx.accounts[0]
    dec = Decision(**ok_decision())
    v = RiskVerdict(
        account_id=acct.id, approved=True, adjusted_qty=3, reasons=["ok"], side="BUY",
        order_type="LIMIT", price=Decimal("71000"),
    )  # fmt: skip
    p = executor.plan_of(
        v, executor.origin_of(), acct, decision=dec, market="KR", exchange=None, price=PRICE,
        decision_id=7, idem_key="7|paper-main|BUY|50_order",
    )  # fmt: skip
    assert isinstance(p, GatePlan) and p.approved and p.reasons == ["ok"]
    (i,) = p.intents
    assert (i.side, i.qty, i.origin, i.order_stage, i.decision_id) == (
        "BUY", 3, "llm", "50_order", 7,
    )  # fmt: skip
    assert i.quote_price == PRICE and i.opinion["action"] == "BUY"
    assert p.verdict_payload["adjusted_qty"] == 3
    rejected = v.model_copy(update={"approved": False, "adjusted_qty": 0})
    assert executor.plan_of(
        rejected, executor.origin_of(), acct, decision=dec, market="KR", exchange=None,
        price=PRICE, decision_id=7, idem_key=None,
    ).intents == []  # fmt: skip


# --- Fix round 1 -------------------------------------------------------------------


def test_invalid_order_request_releases_exec_key(ectx, monkeypatch):
    """주문 요청 검증 실패(잘못된 order_type)는 코어로 가기 전에 나고 실행 키를 놓는다 — 미발송
    키가 남으면 10분 회수 전까지 같은 결정의 재실행이 '실행 키 선점됨' 으로 막힌다."""
    from trader.features.spot import rules

    real = rules.evaluate

    def bad(*a, **kw):
        return real(*a, **kw).model_copy(update={"order_type": "STOP"})

    monkeypatch.setattr(rules, "evaluate", bad)
    results = _run(ectx)
    r = next(x for x in results if x["account_id"] == "paper-main")
    assert "error" in r and ectx.brokers["paper-main"].orders == []
    assert ectx.conn.execute("SELECT COUNT(*) FROM exec_locks").fetchone()[0] == 0
    assert ectx.conn.execute("SELECT COUNT(*) FROM buy_reservations").fetchone()[0] == 0


def test_approve_buy_keeps_pending_when_balance_lookup_fails(ectx):
    """Ruling 7: 승인 재판정의 잔고 조회가 실패하면 판정 불가 — 만료도 실행도 하지 않고 pending."""
    aid = _pending(ectx)
    ectx.brokers["paper-main"] = _broker(ectx.accounts[0], fail={"get_balance"})
    r = executor.approve(ectx, aid)
    assert r["ok"] is False and r["status"] == "pending" and r["reason"] == "balance"
    assert repo.get_approval(ectx.conn, aid)["status"] == "pending"
    assert ectx.brokers["paper-main"].orders == []


def test_approve_hedge_buy_skips_balance_recheck(ectx):
    """헤지 origin 매수 승인은 일일 손실 재판정을 보지 않는다(잔고 실패여도 실행)."""
    modes.set_account_mode(ectx.conn, ectx.accounts[0], "approve")
    dec = Decision(**ok_decision())
    _ingest(ectx.conn, dec)
    results = executor.execute(
        ectx, dec, date=D, market="KR", price=PRICE, dry_run=False, is_hedge=True
    )
    aid = next(x for x in results if x["account_id"] == "paper-main")["approval_id"]
    assert repo.get_approval(ectx.conn, aid)["order"]["origin"] == "hedge"
    ectx.brokers["paper-main"] = _broker(ectx.accounts[0], fail={"get_balance"})
    r = executor.approve(ectx, aid)
    assert r["ok"] is True and len(ectx.brokers["paper-main"].orders) == 1


def test_policy_without_paused_is_fail_closed(ectx):
    """정책만 주입되고 일시정지 콜백이 없으면 일시정지로 본다 — 주문·승인 요청 없음."""
    policy, paused = executor.policy_pair(accounts_api.effective_mode, None)
    assert paused(ectx.conn) is True
    ectx.policy, ectx.paused = policy, paused
    r = next(x for x in _run(ectx) if x["account_id"] == "paper-main")
    assert r["order"] is None and "일시정지" in (r["note"] or "")
    assert ectx.brokers["paper-main"].orders == []
    no_policy, not_paused = executor.policy_pair(None, None)
    assert no_policy(ectx.conn, ectx.accounts[0])[0] == "off" and not_paused(ectx.conn) is False


def test_exec_ctx_respects_notify_enabled_false(project, conn, settings):
    """settings `notify.enabled=false` 면 텔레그램 키가 있어도 spot 실행 알림은 NullNotifier."""
    from types import SimpleNamespace

    from trader.kernel.config import Secrets
    from trader.kernel.notify import NullNotifier
    from trader.kernel.notify.telegram import TelegramNotifier

    secrets = Secrets(TELEGRAM_BOT_TOKEN="t", TELEGRAM_CHAT_ID="c")
    on = SimpleNamespace(settings=settings, conn=conn, ports={}, secrets=secrets)
    assert isinstance(executor.exec_ctx(on).notifier, TelegramNotifier)
    off_settings = settings.model_copy(
        update={"notify": settings.notify.model_copy(update={"enabled": False})}
    )
    off = SimpleNamespace(settings=off_settings, conn=conn, ports={}, secrets=secrets)
    assert isinstance(executor.exec_ctx(off).notifier, NullNotifier)
