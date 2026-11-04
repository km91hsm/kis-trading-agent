"""scalp 판정자: 스레드 풀·클램프·데몬 단일체·에이전트 등록."""

import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tests.features.scalp.conftest import NOW as ENGINE_NOW
from tests.features.scalp.conftest import D, SyncJudge, bars, fill_all, scan_checkpoint
from trader.features.briefing.briefing import AGENT as BRIEFING_AGENT
from trader.features.scalp import engine, judge
from trader.features.scalp.feature import FEATURE
from trader.features.scalp.schemas import ScalpAdvice, ScalpVerdict
from trader.features.scalp.settings import ScalpCfg
from trader.features.scalp.state import advice_key, entry_key, load_state, read_events
from trader.kernel.ledger import db
from trader.kernel.ledger.control import set_flag
from trader.kernel.registry import discover
from trader.kernel.registry.contracts import all_migrations
from trader.kernel.session import agents

NOW = datetime(2026, 11, 4, 1, 0, tzinfo=UTC)
GO = ScalpVerdict(
    go=True,
    size="half",
    take_profit_pct=0.02,
    stop_pct=0.02,
    max_hold_min=120,
    reason="돌파 확인",
    confidence=0.7,
)


@pytest.fixture(autouse=True)
def _reset_judge():
    judge.shutdown_judge()
    yield
    judge.shutdown_judge()


def _deps(settings):
    def factory():
        c = db.connect()
        db.init_schema(c, all_migrations(discover()))
        return c

    return SimpleNamespace(
        settings=settings, conn_factory=factory, stop_event=threading.Event(), now=lambda: NOW
    )


def test_clamp_cannot_widen_stop_or_hold():
    cfg = ScalpCfg(enabled=True, judge=True)
    out = judge.clamp_verdict(GO, cfg)
    assert out["stop_pct"] == cfg.stop_pct and out["max_hold_min"] == cfg.max_hold_min
    assert out["take_profit_pct"] == 0.02  # 0.5~2배 안이면 판정값
    low = judge.clamp_verdict(GO.model_copy(update={"take_profit_pct": 0.001}), cfg)
    assert low["take_profit_pct"] == cfg.take_profit_pct * 0.5
    assert judge.limits(cfg) == {
        "take_profit_pct": cfg.take_profit_pct,
        "stop_pct": cfg.stop_pct,
        "max_hold_min": cfg.max_hold_min,
    }


def test_advice_key_is_per_position():
    a = advice_key("KR", {"ticker": "005930", "entry_at": "2026-10-24T01:00:00+00:00"})
    b = advice_key("KR", {"ticker": "005930", "entry_at": "2026-10-25T01:00:00+00:00"})
    assert a != b and a.startswith("hold:KR:005930:")


def test_judge_expires_unpolled_submissions():
    """회수되지 않은 판정(후보에서 빠진 종목)은 ttl 뒤 버린다."""
    t = [0.0]
    j = judge.ScalpJudge(lambda payload, schema: GO, ttl_s=60, clock=lambda: t[0])
    try:
        j.submit("entry:a", {}, ScalpVerdict)
        j._futs["entry:a"].result(timeout=5)
        t[0] = 61.0
        j.submit("entry:b", {}, ScalpVerdict)
        assert "entry:a" not in j._futs and "entry:a" not in j._at
        assert j.poll("entry:a") is None
        j._futs["entry:b"].result(timeout=5)
        assert j.poll("entry:b") == GO and j._at == {}
    finally:
        j.shutdown()


def test_judge_for_returns_none_when_stop_event_set(project, settings):
    deps = _deps(settings)
    j = judge.judge_for(deps)
    assert judge.judge_for(deps) is j  # 데몬 수명 동안 하나
    deps.stop_event.set()
    assert j._ask({"ticker": "005930"}, ScalpVerdict) is None


def test_judge_for_uses_fake_under_fake_claude(monkeypatch, project, settings):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    j = judge.judge_for(_deps(settings))
    v = j._ask({"ticker": "005930"}, ScalpVerdict)
    assert isinstance(v, ScalpVerdict) and v.go is False and v.size == "half"
    a = j._ask({"ticker": "005930"}, ScalpAdvice)
    assert isinstance(a, ScalpAdvice) and a.action == "hold"


def test_agent_registered_with_common_rules():
    assert FEATURE.agents == (judge.AGENT,)
    loaded = agents.load_agent(judge.AGENT)
    assert loaded.model == "haiku"
    rules = agents.common_rules(loaded.prompt)
    assert len(rules) == 6
    assert rules == agents.common_rules(agents.load_agent(BRIEFING_AGENT).prompt)


class _Regime:
    def load(self, conn, market):
        return {"state": "neutral", "count": 2}

    def view_today(self, conn, market, today):
        raise RuntimeError("판단 없음")


class _Book:
    asks = [(f"{100 + i}", 10 + i) for i in range(7)]
    bids = [(f"{99 - i}", 20 + i) for i in range(7)]


def test_judge_context_sections_and_failures():
    class Client:
        def get_orderbook_kr(self, ticker):
            return _Book()

        def get_orderbook_us(self, ticker, exchange):
            raise RuntimeError("호가 실패")

    ports, kw = {"regime": _Regime()}, {"ticker": "005930", "today": NOW.date()}
    kr = judge.judge_context(None, ports, Client(), market="KR", exchange=None, **kw)
    assert len(kr["orderbook"]["asks"]) == 5 and kr["orderbook"]["bids"][0] == ["99", 20]
    assert kr["news"] == [] and kr["regime"] == {"state": "neutral", "count": 2}
    assert kr["market_view"] is None  # 실패한 절은 None
    us = judge.judge_context(None, ports, Client(), market="US", exchange="NAS", **kw)
    assert us["orderbook"] is None


def test_judge_context_regime_passes_stored_record(conn, settings):
    """T4-R1: 국면 절은 저장된 기록 그대로 — 없으면 None/None(‘bull’/0 으로 기울이지 않는다)."""
    from trader.features.regime import regime as regime_mod
    from trader.features.regime.ports import RegimePortImpl
    from trader.kernel.ledger import control

    class Client:
        def get_orderbook_kr(self, ticker):
            raise RuntimeError("호가 없음")

    ports = {"regime": RegimePortImpl(settings)}
    kw = {"market": "KR", "ticker": "005930", "exchange": None, "today": NOW.date()}
    out = judge.judge_context(conn, ports, Client(), **kw)
    assert out["regime"] == {"state": None, "count": None}
    control.set_flag(conn, f"{regime_mod.FLAG_PREFIX}KR", {"state": "bear", "count": 3})
    out = judge.judge_context(conn, ports, Client(), **kw)
    assert out["regime"] == {"state": "bear", "count": 3}


# --- 엔진 연동: 비동기 판정·다음 틱 회수·가격 이탈·재시작 (Task 5) ---------------------------

NOGO = GO.model_copy(update={"go": False, "reason": "추격"})


@pytest.fixture
def judged_ctx(scalp_ctx, settings, market, project):
    """판정자 켠 단타 컨텍스트(005930 신호·현재가 10050). `judged_ctx(answer, **kw)`."""

    def make(answer, **kw):
        scan_checkpoint(project, "KR", ["005930"])
        market.minutes["005930"] = bars(burst=True)
        market.prices["005930"] = Decimal("10050")
        cfg = ScalpCfg(enabled=True, judge=True)
        return scalp_ctx(
            settings=settings.model_copy(update={"scalp": cfg}), judge=SyncJudge(answer), **kw
        )

    return make


def _st(project):
    return load_state(project / "state", D, "KR")


def test_judge_go_enters_next_tick(judged_ctx, project):
    ctx = judged_ctx(GO)
    out = engine.tick(ctx, market="KR", today=D)
    assert out["entries"] == []  # 이번 틱은 제출만
    key, payload, schema = ctx.judge.seen[0]
    assert key == entry_key("KR", D, "005930") and schema is ScalpVerdict
    assert len(payload["bars"]) == 20
    out = engine.tick(ctx, market="KR", today=D)
    assert [e["ticker"] for e in out["entries"]] == ["005930"]
    pos = _st(project)["open"][0]
    # half → 비중 2.5% (모의 계좌마다)
    assert pos["judged"] is True and pos["qty"] == {"paper-main": 24, "paper-two": 24}
    assert pos["weight"] == ctx.settings.scalp.max_weight_pct * 0.5  # 일일 손실 계산용 실제 비중


def test_judge_nogo_no_entry(judged_ctx, project):
    ctx = judged_ctx(NOGO)
    engine.tick(ctx, market="KR", today=D)
    out = engine.tick(ctx, market="KR", today=D)
    st = _st(project)
    assert out["entries"] == [] and st["open"] == [] and "005930" in st["done"]


def test_judge_drift_discards(judged_ctx, market, project):
    ctx = judged_ctx(GO)
    engine.tick(ctx, market="KR", today=D)
    market.prices["005930"] = Decimal("10200")  # 신호가 10050 대비 +1.5%
    out = engine.tick(ctx, market="KR", today=D)
    assert out["entries"] == [] and "005930" in _st(project)["done"]


def test_pending_lost_after_restart_no_entry(judged_ctx, project):
    ctx = judged_ctx(GO)
    engine.tick(ctx, market="KR", today=D)
    ctx.judge = SyncJudge(GO)  # 재시작: future 가 없다
    out = engine.tick(ctx, market="KR", today=D)
    assert out["entries"] == [] and _st(project)["pending"] == {}


def test_judge_timeout_no_entry(judged_ctx, project):
    ctx = judged_ctx(GO)
    engine.tick(ctx, market="KR", today=D)

    class Never(SyncJudge):
        def poll(self, key):
            return "pending"

    ctx.judge = Never(GO)
    ctx.now = lambda: ENGINE_NOW + timedelta(seconds=10)
    assert engine.tick(ctx, market="KR", today=D)["entries"] == []
    assert "005930" in _st(project)["pending"]  # 아직 기다린다
    ctx.now = lambda: ENGINE_NOW + timedelta(seconds=120)
    engine.tick(ctx, market="KR", today=D)
    st = _st(project)
    assert st["pending"] == {} and st["open"] == []


def test_stop_loss_exits_immediately_while_advice_pending(
    judged_ctx, market, brokers, conn, project
):
    ctx = judged_ctx(
        lambda k, p, s: (
            GO if s is ScalpVerdict else ScalpAdvice(action="hold", reason="r", confidence=0.5)
        ),
    )
    engine.tick(ctx, market="KR", today=D)
    engine.tick(ctx, market="KR", today=D)

    class Pending(SyncJudge):
        def poll(self, key):
            return "pending"

    fill_all(conn, brokers, market)
    ctx.judge = Pending(GO)
    market.prices["005930"] = Decimal("9800")  # 손절선 −1% 아래
    engine.tick(ctx, market="KR", today=D)  # 판정 대기와 무관하게 청산 주문
    fill_all(conn, brokers, market)
    out = engine.tick(ctx, market="KR", today=D)
    assert out["exits"] and out["exits"][0]["reason"] == "stop"


def test_advice_exit_closes_early(judged_ctx, brokers, market, conn, project):
    advice = ScalpAdvice(action="exit", reason="VWAP 이탈", confidence=0.8)
    ctx = judged_ctx(lambda k, p, s: GO if s is ScalpVerdict else advice)
    engine.tick(ctx, market="KR", today=D)
    engine.tick(ctx, market="KR", today=D)  # 진입
    fill_all(conn, brokers, market)
    ctx.now = lambda: ENGINE_NOW + timedelta(minutes=6)
    engine.tick(ctx, market="KR", today=D)  # 조언 제출(동기 판정)
    engine.tick(ctx, market="KR", today=D)  # 회수 → 조기 청산 주문
    fill_all(conn, brokers, market)
    out = engine.tick(ctx, market="KR", today=D)
    assert out["exits"] and out["exits"][0]["reason"] == "judge_exit"


def test_report_go_vs_nogo_summary(scalp_ctx, settings, market, brokers, conn, project):
    scan_checkpoint(project, "KR", ["005930", "000660"])
    market.minutes.update({"005930": bars(burst=True), "000660": bars(burst=True)})
    market.prices.update({"005930": Decimal("10050"), "000660": Decimal("10050")})
    cfg = ScalpCfg(enabled=True, judge=True, max_positions=2)
    ctx = scalp_ctx(
        settings=settings.model_copy(update={"scalp": cfg}),
        judge=SyncJudge(lambda k, p, s: GO if "005930" in k else NOGO),
    )
    engine.tick(ctx, market="KR", today=D)
    engine.tick(ctx, market="KR", today=D)  # 005930 GO 진입, 000660 NO_GO
    fill_all(conn, brokers, market)
    market.prices["005930"] = Decimal("10300")
    ctx.judge = SyncJudge(ScalpAdvice(action="hold", reason="r", confidence=0.5))
    engine.tick(ctx, market="KR", today=D)  # 익절(+2.5% ≥ 판정 2%)
    fill_all(conn, brokers, market)
    engine.tick(ctx, market="KR", today=D)  # 청산 체결 확인
    after = bars(burst=True, price="10200")  # 신호 뒤 봉: +1.5% 익절
    r = engine.report(
        project / "state",
        market="KR",
        days=1,
        today=D,
        judge={"cfg": ScalpCfg(), "close_hm": "15:30", "minutes_fn": lambda t: after},
    )
    j = r["judge"]
    assert j["counts"] == {"go": 1, "no_go": 1, "none": 0}
    assert j["go_trades"] == 1 and j["go_avg_ret"] > 0
    assert j["no_go_measured"] in (0, 1)


def test_late_completed_verdict_after_deadline_is_dropped(judged_ctx, project):
    """대기열에서 늦게 끝난 판정(제출 뒤 시한 초과)은 완료돼 있어도 쓰지 않는다."""
    ctx = judged_ctx(GO)
    engine.tick(ctx, market="KR", today=D)
    ctx.now = lambda: ENGINE_NOW + timedelta(seconds=ctx.settings.scalp.judge_timeout_s + 5)
    out = engine.tick(ctx, market="KR", today=D)
    st = _st(project)
    assert out["entries"] == [] and st["open"] == [] and st["pending"] == {}


def test_verdict_after_close_cutoff_no_entry(judged_ctx, project):
    """마감 전 신규 진입 금지 시각을 넘긴 뒤 회수한 GO 는 진입하지 않는다."""
    ctx = judged_ctx(GO)
    engine.tick(ctx, market="KR", today=D)
    ctx.close_hm = "10:35"  # 지금 10:30 KST, close_before_min 이 이 시각을 덮는다
    out = engine.tick(ctx, market="KR", today=D)
    st = _st(project)
    assert out["entries"] == [] and st["open"] == [] and st["pending"] == {}


def test_daily_loss_uses_position_weight(judged_ctx, project):
    """half 진입 손실은 full 비중이 아니라 실제 비중으로 일일 손실에 들어간다."""
    ctx = judged_ctx(GO)
    cfg = ctx.settings.scalp
    # full 비중이면 한도를 넘고 half 비중이면 넘지 않는 손실
    ret = -cfg.daily_stop_pct / (cfg.max_weight_pct * 0.75)
    st = _st(project)
    st["closed"] = [{"ticker": "000660", "ret": ret, "weight": cfg.max_weight_pct * 0.5}]
    engine.save_state(project / "state", D, "KR", st)
    engine.tick(ctx, market="KR", today=D)
    assert not _st(project).get("halted")


def test_judge_throttled_is_event_not_entry(judged_ctx, project):
    """Review Focus 4: 판정자가 상한·감속으로 None 을 돌려주면 진입하지 않고 judge 사건만 남긴다."""
    ctx = judged_ctx(None)
    engine.tick(ctx, market="KR", today=D)  # 제출(동기 판정 → None)
    out = engine.tick(ctx, market="KR", today=D)  # 회수
    st = _st(project)
    assert out["entries"] == [] and st["open"] == [] and "005930" in st["done"]
    ev = [e for e in read_events(project / "state", D) if e["kind"] == "judge"]
    assert len(ev) == 1 and ev[0]["go"] is None and ev[0]["ticker"] == "005930"


def test_ask_returns_none_when_cap_reached_or_throttled(project, settings, conn, monkeypatch):
    """Review Focus 4: (a) 오늘 scalp_judge 호출이 하루 상한(60)이면 None, (b) 구독 사용률 감속이
    거절하면 None — 어느 쪽도 claude 를 띄우지 않는다(FAKE_CLAUDE 없이 검사, 실행은 막아 둔다)."""
    from trader.kernel.session import calls, quick, usage

    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    spawned = []
    monkeypatch.setattr(quick, "_spawn", lambda *a, **k: spawned.append(k) or 1 / 0)
    now = ENGINE_NOW
    # 개발자 홈의 상태줄 관측 파일을 읽지 않게(원장 관측만)
    llm = settings.llm.model_copy(update={"usage_file": None})
    settings = settings.model_copy(update={"llm": llm})
    deps = _deps(settings)
    deps.now = lambda: now
    ask = judge.judge_for(deps)._ask
    # (a) 하루 상한
    for i in range(settings.scalp.judge_daily_cap):
        cid = calls.start_call(
            conn, None, agent="scalp_judge", model="haiku", ticker="005930", market=None,
            session_id=f"s{i}", now=now,
        )  # fmt: skip
        calls.finish_call(conn, cid, status="ok")
    assert calls.calls_today(conn, "scalp_judge", now) == settings.scalp.judge_daily_cap
    assert ask({"ticker": "005930"}, ScalpVerdict) is None
    conn.execute("DELETE FROM agent_calls")
    conn.commit()
    # (b) 사용량 감속: 5시간 창 사용률 99% (hard_pct 이상) 관측
    set_flag(conn, usage.THROTTLE_FLAG, True)
    resets = int((now + timedelta(hours=2)).timestamp())
    info = {
        "status": "allowed",
        "unifiedWindows": {
            "five_hour": {"utilization": 0.99, "resetsAt": resets},
            "seven_day": {"utilization": 0.1, "resetsAt": resets},
        },
    }
    usage.record_rate_limit(conn, info, now=now - timedelta(minutes=5))
    ok, why = usage.session_allowance(conn, settings, kind="scalp_judge", now=now)
    assert not ok and why
    assert ask({"ticker": "005930"}, ScalpVerdict) is None
    assert spawned == []  # 어느 쪽도 세션을 띄우지 않았다
