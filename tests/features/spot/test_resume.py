"""spot 재평가(resume) — `31_decision_r<n>` 제출·트리거 가격 게이트·previous_decision 테스트.

옛 `tests/orchestrator/test_resume.py` 에서 이식(생성자·fixture 만 바꿨다). memory 엔트리 1건은
M8 에서 `ports["evaluation"]` 으로 더했고, FAKE 재평가 세션은 `test_fake.py` 에 있다."""

from decimal import Decimal

import pytest

from tests.features.spot.conftest import T0, D, make_account
from tests.features.spot.helpers import ok_decision, ok_report, ok_turn
from trader.features.accounts import api as accounts_api
from trader.features.accounts.ports import AccountsPortImpl
from trader.features.accounts.settings import to_broker_account
from trader.features.spot import ledger, resume, tools
from trader.features.spot.schemas import Decision
from trader.kernel.broker.fake import FakeBroker
from trader.kernel.broker.models import Cash
from trader.kernel.config import Secrets
from trader.kernel.lane.types import LaneContext
from trader.kernel.notify import NullNotifier
from trader.kernel.session import run_store
from trader.kernel.session.run_store import list_checkpoints, read_checkpoint, run_dir
from trader.kernel.session.runner import SessionResult


@pytest.fixture
def ctx(project, conn, settings):
    accounts = [make_account("paper-main", "paper", "auto", markets=["KR"])]
    cash = {"KR": Cash(currency="KRW", available=Decimal("10000000"), total=Decimal("10000000"))}
    return tools.ToolContext(
        settings=settings,
        accounts=accounts,
        conn=conn,
        state=project / "state",
        now=lambda: T0,
        brokers={a.id: FakeBroker(to_broker_account(a), cash=cash) for a in accounts},
        dry_run=False,
        policy=accounts_api.effective_mode,
        paused=accounts_api.is_paused,
    )


def _prepare(ctx):
    """아침 분석(30_decision BUY 14주)까지 끝난 상태. 아침 시세 71500."""
    d = run_dir(D, "KR", "005930", ctx.state)
    run_store.write_checkpoint(
        d,
        "00_inputs",
        {
            "meta": {"ticker": "005930", "market": "KR", "asof": T0.isoformat()},
            "technical": {"quote": {"data": {"price": "71500"}, "received_at": "", "error": None}},
            "fundamental": {},
            "news_macro": {},
            "sentiment": {},
            "errors": [],
        },
    )
    for role in ("technical", "fundamental"):
        tools.checkpoint_write(
            ctx,
            ticker="005930",
            market="KR",
            date=D,
            stage=f"10_analyst_{role}",
            payload=ok_report(role=role),
            agent="a",
        )
    for side in ("bull", "bear"):
        tools.checkpoint_write(
            ctx,
            ticker="005930",
            market="KR",
            date=D,
            stage=f"20_debate_r0_{side}",
            payload=ok_turn(side),
            agent="r",
        )
    tools.checkpoint_summarize(ctx, ticker="005930", market="KR", date=D)
    r = tools.submit_decision(ctx, ticker="005930", market="KR", date=D, payload=ok_decision())
    assert r["ok"] and len(ctx.brokers["paper-main"].orders) == 1  # 최초 BUY 14주
    return d


def _trigger(ctx, d, price, kind="take_profit"):
    return ledger.insert_trigger(
        ctx.conn,
        market="KR",
        ticker="005930",
        kind=kind,
        detail={},
        price=None if price is None else Decimal(price),
        decision_id=ledger.decision_id(ctx.conn, D, "KR", "005930"),
        account_id="paper-main",
        created_at=T0.isoformat(),
    )


def test_submit_decision_resume_stage_executes_new_decision(ctx):
    _prepare(ctx)
    r = tools.submit_decision(
        ctx,
        ticker="005930",
        market="KR",
        date=D,
        stage="31_decision_r1",
        payload=ok_decision(action="SELL", target_weight=0.0),
    )
    assert r["ok"] and r["stage"] == "31_decision_r1"
    assert "31_decision_r1" in list_checkpoints(run_dir(D, "KR", "005930", ctx.state))
    # 재평가 SELL 은 기존 BUY 주문이 있어도 나간다 (같은 방향만 중복 방지)
    assert len(ctx.brokers["paper-main"].orders) == 1  # 보유 없음 → SELL 은 거부(미보유)
    ex = r["executions"][0]
    assert ex["approved"] is False and any("보유" in s for s in ex["reasons"])
    assert ctx.conn.execute("SELECT action FROM decisions").fetchone()["action"] == "SELL"
    bad = tools.submit_decision(
        ctx, ticker="005930", market="KR", date=D, stage="32_x", payload=ok_decision()
    )
    assert bad["ok"] is False


def test_resume_stage_requires_original_decision(ctx):
    r = tools.submit_decision(
        ctx, ticker="005930", market="KR", date=D, stage="31_decision_r1", payload=ok_decision()
    )
    assert r["ok"] is False and "30_decision" in r["error"]


def test_decision_schema_unchanged_for_resume():
    assert Decision(**ok_decision()).action == "BUY"


def test_resume_stage_writes_round_specific_risk_and_order_checkpoints(ctx):
    """재평가(31_decision_r<n>)의 판정·주문 체크포인트는 오늘 분석의 40_/50_ 을 덮어쓰지 않는다."""
    d = _prepare(ctx)
    before = (d / "40_risk_check_paper-main.json").read_text(encoding="utf-8")
    r = tools.submit_decision(
        ctx,
        ticker="005930",
        market="KR",
        date=D,
        stage="31_decision_r1",
        payload=ok_decision(action="SELL", target_weight=0.0),
    )
    assert r["ok"]
    stages = list_checkpoints(d)
    assert "41_risk_check_r1_paper-main" in stages
    assert (d / "40_risk_check_paper-main.json").read_text(encoding="utf-8") == before


def test_resume_uses_trigger_price_for_stop_loss_check_and_gate(ctx):
    """재평가는 장중 트리거 가격이 현재가다. 아침 00_inputs 시세로 손절선을 검사하면 '현재가 아래'
    규칙을 지킨 트레이더의 결정(아침 가격보다 높은 손절선)이 거부된다."""
    _prepare(ctx)  # 아침 시세 71500
    _trigger(ctx, None, "80000")
    r = tools.submit_decision(
        ctx,
        ticker="005930",
        market="KR",
        date=D,
        stage="31_decision_r1",
        payload=ok_decision(action="HOLD", target_weight=0.0, stop_loss=75000.0),
    )
    assert r["ok"], r
    assert r.get("price") == "80000"


def test_trader_bundle_carries_previous_decision_in_resume(ctx):
    """트리거 재평가의 트레이더 입력에는 원래 결정(30_decision·최신 31_)이 들어가야 한다.
    md 가 '원래 결정을 전제로' 라고 하는데 묶음에 없으면 트레이더가 아침 리포트로 다시 추측한다."""
    _prepare(ctx)
    b = tools.checkpoint_bundle(ctx, ticker="005930", market="KR", date=D, kind="trader")
    assert b["ok"] and b["data"]["previous_decision"]["action"] == "BUY"
    tools.submit_decision(
        ctx,
        ticker="005930",
        market="KR",
        date=D,
        stage="31_decision_r1",
        payload=ok_decision(action="TRIM", target_weight=0.05),
    )
    b2 = tools.checkpoint_bundle(ctx, ticker="005930", market="KR", date=D, kind="trader")
    assert b2["data"]["previous_decision"]["action"] == "TRIM"
    assert b2["data"]["previous_decision_stage"] == "31_decision_r1"


class _Port(AccountsPortImpl):
    """합성 계좌 + 테스트가 쥔 FakeBroker 를 그대로 돌려주는 계좌 포트(네트워크 없음)."""

    def __init__(self, ctx):
        super().__init__(ctx.accounts, Secrets())
        self._brokers = ctx.brokers

    def brokers(self):
        return list(self._brokers.values())

    def market_client(self):
        return None


def test_reevaluate_news_trigger_without_price_uses_inputs_quote(ctx, monkeypatch):
    """Phase 23: 뉴스 트리거(kind=news, price 없음)도 재평가 세션이 돌고 31_decision_r1 을 남긴다.
    현재가는 트리거 가격이 아니라 00_inputs 시세다."""
    d = _prepare(ctx)
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    tid = _trigger(ctx, d, None, kind="news")
    assert ledger.latest_trigger_price(ctx.conn, "KR", "005930", D) is None
    lane = LaneContext(
        settings=ctx.settings,
        conn=ctx.conn,
        state=ctx.state,
        now=lambda: T0,
        dry_run=False,
        notifier=NullNotifier(),
        ports={"accounts": _Port(ctx)},
    )
    res = resume.reevaluate(
        lane,
        market="KR",
        date=D,
        ticker="005930",
        exchange=None,
        trigger_id=tid,
        trigger={"kind": "news", "price": None, "detail": {"count": 1}},
    )
    assert res.ok and "31_decision_r1" in list_checkpoints(d)
    assert ctx.conn.execute("SELECT handled_at FROM triggers WHERE id=?", (tid,)).fetchone()[0]


def test_trigger_priced_resume_skips_chase_check_on_morning_change_pct(ctx):
    """트리거 가격 재평가는 아침 시세의 등락률(+12%)로 급등 추격을 판정하지 않는다(PR #26 리뷰)."""
    d = _prepare(ctx)
    inputs = read_checkpoint(d, "00_inputs")
    inputs["technical"]["quote"]["data"]["change_pct"] = 0.12
    run_store.write_checkpoint(d, "00_inputs", inputs)
    _trigger(ctx, d, "72000", kind="move_7pct")
    r = tools.submit_decision(
        ctx,
        ticker="005930",
        market="KR",
        date=D,
        stage="31_decision_r1",
        payload=ok_decision(action="ADD", target_weight=0.2, limit_price=72000.0),
    )
    assert r["ok"], r
    rc = read_checkpoint(d, "41_risk_check_r1_paper-main")["data"]
    assert not any("급등 추격" in x for x in rc["reasons"])


def test_resume_records_separate_memory_entry_and_keeps_morning_entry(ctx, ports):
    """전체 검토 L2: 재평가가 아침 엔트리의 action 을 덮어쓰면 아침 판단의 채점이 사라진다."""
    from trader.features.evaluation.memory import store

    ctx.ports = ports
    _prepare(ctx)
    r = tools.submit_decision(
        ctx,
        ticker="005930",
        market="KR",
        date=D,
        stage="31_decision_r1",
        payload=ok_decision(action="SELL", target_weight=0.0),
    )
    assert r["ok"]
    rows = ctx.conn.execute(
        "SELECT id, round, decision, held_at_decision FROM memory_entries ORDER BY round"
    ).fetchall()
    assert [x["round"] for x in rows] == [0, 1]
    assert store.get_entry(ctx.conn, rows[0]["id"])["decision"]["action"] == "BUY"
    assert store.get_entry(ctx.conn, rows[1]["id"])["decision"]["action"] == "SELL"
    assert r["memory_entry_id"] == rows[1]["id"]
    # 아침엔 보유 없음(판정 때 잔고), 결정 시점 보유가 기록된다
    assert rows[0]["held_at_decision"] == 0


def test_resume_change_pct_is_trigger_price_vs_previous_close(ctx):
    """전체 검토 2차 2-O6: 트리거 가격 재평가의 당일 등락률은 트리거 가격 ÷ 직전 완성 봉 종가 − 1.
    아침 시세의 등락률을 버리고 None 으로 두면 장중 +12% 급등을 쫓는 BUY 가 추격 금지를 지난다."""
    d = _prepare(ctx)
    inputs = read_checkpoint(d, "00_inputs")
    inputs["technical"]["quote"]["data"]["change_pct"] = 0.12  # 아침 시세(쓰지 않는다)
    inputs["technical"]["daily"] = {
        "data": [
            {"date": "2026-10-07", "close": "60000"},
            {"date": "2026-10-08", "close": "65000"},
            {"date": "2026-10-09", "close": "71500"},  # 오늘 봉(미완성)은 제외
        ],
        "received_at": "",
        "error": None,
    }
    run_store.write_checkpoint(d, "00_inputs", inputs)
    for n, price in ((1, "66000"), (2, "73000")):  # +1.5% / +12.3% (65000 기준)
        _trigger(ctx, d, price, kind="move")
        r = tools.submit_decision(
            ctx,
            ticker="005930",
            market="KR",
            date=D,
            stage=f"31_decision_r{n}",
            payload=ok_decision(),
        )
        assert r["ok"] and r["price"] == price
        reasons = r["executions"][0]["reasons"]
        chased = any("당일 등락률 +12.3%" in s for s in reasons)
        assert chased is (n == 2), reasons


def test_handled_marker_success_and_failure_prefix():
    """성공이면 session_id, 실패면 '실패:' 접두사 문자열(옛 shape — 운영일 1회 제한에서 빠진다)."""
    ok = SessionResult(ok=True, session_id="s1")
    assert resume.handled_marker(ok) == "s1"
    bad = SessionResult(ok=False, session_id="s2", error="타임아웃")
    assert resume.handled_marker(bad) == "실패: 타임아웃 (s2)"
    assert resume.handled_marker(SessionResult(ok=False)) == "실패: unknown (-)"
