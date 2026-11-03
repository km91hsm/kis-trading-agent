"""regime 데몬 잡·큐 kind: `{market}_regime`·`ksd_events`·`market_view`.

옛 `tests/test_daemon_hedge.py` 1·5·6·(7+14 통합)·8·9·10·12·13·15·18·19 이식. 치환(플랜 「바꾸는 옛
단언」): `daemon._regime_body`→`jobs.regime_update`, `_market_view_body`→`jobs.market_view_body`,
`_regime_apply_view`→`jobs.apply_view`, `_mature_market_views`(reflect 잡)→`jobs.mature_views`,
`_proxy_candles`→`inputs.proxy_candles`, `_ksd_body`→`jobs.ksd_body`, `_deps`→`DaemonDeps` +
`ports["accounts"]` 가짜 포트(`FakeAccountsPort`), `Settings(...)`→`settings.model_copy`,
`tools.sessions_today` monkeypatch→`kernel.session.calls.sessions_today`. DaemonDeps 는 frozen 이라
속성 대입 대신 `dataclasses.replace`. 5번의 헤지 진입 단언(`_hedge_body`)은 Task 7(`spot.hedge`)
몫이라 여기서 뺐다."""

import threading
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tests.features.accounts.conftest import make_account
from tests.kernel.daemon.conftest import FakeAccountsPort
from trader.features.regime import blackout, inputs, jobs, regime
from trader.features.regime import market_view as mv
from trader.features.regime.feature import FEATURE
from trader.features.regime.settings import BlackoutCfg, HedgeCfg
from trader.kernel.broker.fake import FakeBroker, FakeMarket
from trader.kernel.broker.models import Candle, Cash
from trader.kernel.cli.boot import build_registry
from trader.kernel.config import Secrets
from trader.kernel.config.loader import load
from trader.kernel.daemon import jobs as daemon_jobs
from trader.kernel.daemon import queue as jobq
from trader.kernel.daemon.runner import DaemonDeps
from trader.kernel.ledger import control, db
from trader.kernel.registry import discover

NOW = datetime(2026, 10, 24, 1, 0, tzinfo=UTC)  # 10:00 KST
CFG = HedgeCfg(ma_days=20, momentum_days=5, exit_streak_days=1)
BEAR = [100] * 30 + [95, 90, 85, 80, 75, 70]


def _candles(closes):
    d0 = date(2026, 9, 1)
    return [
        Candle(date=d0 + timedelta(days=i), open=c, high=c, low=c, close=Decimal(str(c)), volume=1)
        for i, c in enumerate(closes)
    ]


class Notes:
    def __init__(self):
        self.msgs = []

    def send(self, text, *, escape=True):
        self.msgs.append(text)
        return True


def _hedge_deps(project, settings, *, closes, prices=None) -> DaemonDeps:
    market = FakeMarket(prices=prices or {}, candles={"069500": _candles(closes)}, now=lambda: NOW)
    acct = make_account("paper-main", "paper", "auto", ["KR", "US"])
    broker = FakeBroker(
        acct,
        cash={"KR": Cash(currency="KRW", available=Decimal("10000000"), total=Decimal("10000000"))},
    )
    port = FakeAccountsPort(market_client=market, brokers={"paper-main": broker}, accounts=[acct])
    return DaemonDeps(
        settings=settings.model_copy(update={"hedge": CFG}),
        schedule=None,
        conn_factory=db.connect,
        state=project / "state",
        now=lambda: NOW,
        markets=["KR", "US"],
        gates={},
        stop_event=threading.Event(),
        dry_run=True,
        notifier=Notes(),
        secrets=Secrets(),
        ports={"accounts": port},
    )


def _with(deps, **hedge) -> DaemonDeps:
    return replace(deps, settings=deps.settings.model_copy(update={"hedge": HedgeCfg(**hedge)}))


def _mc(deps):
    return deps.ports["accounts"].market


def test_regime_body_updates_flag_and_notifies_transition(project, settings, conn):
    deps = _hedge_deps(project, settings, closes=BEAR)
    out = jobs.regime_update(deps, "KR")
    assert out["state"] == "bear" and out["count"] == 2
    assert regime.load(conn, "KR")["state"] == "bear"
    assert any("[국면 전환] KR — → 하락" in m for m in deps.notifier.msgs)
    # 데이터가 없으면 error 로 보고하고 직전 상태 유지
    _mc(deps).candles = {}
    assert "error" in jobs.regime_update(deps, "KR") and regime.load(conn, "KR")["state"] == "bear"
    deps = _with(deps, enabled=False)
    assert jobs.regime_update(deps, "KR") == {"skipped": "hedge.enabled=false"}


def test_market_view_body_runs_fake_session_once_and_notifies(project, settings, conn, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    deps = _hedge_deps(project, settings, closes=BEAR)
    jobs.regime_update(deps, "KR")
    out = jobs.market_view_body(deps, "KR")
    assert out["view"]["stance"] == "RISK_OFF" and out["view"]["hedge_weight"] == 0.1
    assert any("[시장 뷰] KR 위험 회피" in m for m in deps.notifier.msgs)
    assert conn.execute("SELECT COUNT(*) FROM runs WHERE kind='market'").fetchone()[0] == 1
    # 같은 날 다시 부르면 세션을 띄우지 않는다
    assert jobs.market_view_body(deps, "KR")["skipped"] == "오늘 이미 제안 있음"
    assert regime.summary(conn, ["KR"], today=NOW.date())[0]["view"]["stance"] == "RISK_OFF"
    assert "제안 10%" in regime.line(conn, ["KR"], today=NOW.date())


def test_market_view_body_skips_when_disabled_or_cap_hit(project, settings, conn, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    deps = _hedge_deps(project, settings, closes=[100] * 36)
    off = _with(deps, market_session=False)
    assert "market_session" in jobs.market_view_body(off, "KR")["skipped"]
    monkeypatch.setattr("trader.kernel.session.calls.sessions_today", lambda conn, now: 99)
    out = jobs.market_view_body(deps, "KR")
    assert out["skipped"] == "세션 상한" and not deps.notifier.msgs


def test_regime_job_runs_regime_view_apply_mature_in_order(project, settings, monkeypatch):
    """옛 scan_analyze 의 regime → view → apply 순서(7·14 통합) + 제안 채점."""
    deps = _hedge_deps(project, settings, closes=[100] * 36)
    order = []
    monkeypatch.setattr(jobs, "regime_update", lambda d, m: order.append("regime") or {})
    monkeypatch.setattr(jobs, "market_view_body", lambda d, m: order.append("view") or {})
    monkeypatch.setattr(jobs, "apply_view", lambda d, m: order.append("apply") or {})
    monkeypatch.setattr(jobs, "mature_views", lambda d, c: order.append("mature") or 0)
    jobs.regime_job(deps, SimpleNamespace(market="KR"))
    assert order == ["regime", "view", "apply", "mature"]


def test_market_view_queue_kind():
    kinds = jobq.kinds_of(discover())
    assert jobq.validate(kinds, "market_view", {"market": "US"}) == {"market": "US"}
    assert kinds["market_view"].lane == "slow" and kinds["market_view"].label == "시장 세션"
    with pytest.raises(ValueError):
        jobq.validate(kinds, "market_view", {"market": "JP"})


def test_market_view_body_respects_market_session_cap(project, settings, conn, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    deps = _hedge_deps(project, settings, closes=[100] * 36)
    llm = deps.settings.llm.model_copy(update={"market_session_cap": 0})
    deps = replace(deps, settings=deps.settings.model_copy(update={"llm": llm}))
    out = jobs.market_view_body(deps, "KR")
    assert out["skipped"] == "세션 상한"


def test_reflect_body_matures_market_views(project, settings, conn):
    deps = _hedge_deps(project, settings, closes=[100 - i for i in range(10)])
    first_date = _mc(deps).candles["069500"][0].date
    mv.record_view(
        conn,
        today=first_date,
        market="KR",
        view={"stance": "RISK_OFF", "hedge_weight": 0.1, "horizon_days": 2},
    )
    assert jobs.mature_views(deps, conn) == 1
    assert conn.execute("SELECT hit FROM market_views").fetchone()["hit"] == 1


def test_proxy_candles_drop_pre_open_stub():
    """개장 전 거래량 0 스텁 봉은 국면 proxy 일봉에서 버린다 (리뷰 F15)."""
    D_ = Decimal
    good = Candle(
        date=date(2026, 10, 23), open=D_(1), high=D_(1), low=D_(1), close=D_(100), volume=10
    )
    stub = Candle(
        date=date(2026, 10, 24), open=D_(1), high=D_(1), low=D_(1), close=D_(50), volume=0
    )
    mc = SimpleNamespace(get_daily_kr=lambda *a, **k: [good, stub])
    assert inputs.proxy_candles(mc, "KR", CFG, now=NOW) == [good]


def test_regime_apply_view_turns_strategist_risk_off_into_fourth_signal(
    project, settings, conn, monkeypatch
):
    """Phase 19: 시장 세션 제안이 RISK_OFF(확신 ≥ 기준)면 전략가 신호로 재판정해 1/3 → bear 전환,
    같은 날 재호출 안전, RISK_ON 이면 신호를 끈다."""
    deps = _hedge_deps(project, settings, closes=[100] * 30 + [99.5, 99, 98.5, 98, 97.5, 97])
    # 전체 검토 L8 뒤 agent_signal 기본 끔 — Phase 19 동작은 켜서 확인
    deps = replace(
        deps,
        settings=deps.settings.model_copy(
            update={"hedge": CFG.model_copy(update={"agent_signal": True})}
        ),
    )
    out = jobs.regime_update(deps, "KR")
    assert out["state"] == "neutral" and out["count"] == 1
    monkeypatch.setattr(
        mv,
        "load_view",
        lambda conn_, m, d: {"stance": "RISK_OFF", "confidence": 0.8, "hedge_weight": 0.1},
    )
    r = jobs.apply_view(deps, "KR")
    assert r["state"] == "bear" and r["agent_risk_off"] is True
    assert any("[국면 전환] KR 중립 → 하락 (1/3+전략가)" in m for m in deps.notifier.msgs)
    n = len(deps.notifier.msgs)
    assert jobs.apply_view(deps, "KR")["state"] == "bear" and len(deps.notifier.msgs) == n
    assert regime.load(conn, "KR")["streak"] == 0
    # 확신이 낮으면 지표로 쓰지 않는다 → 다시 neutral
    monkeypatch.setattr(
        mv,
        "load_view",
        lambda conn_, m, d: {"stance": "RISK_OFF", "confidence": 0.5, "hedge_weight": 0.1},
    )
    assert jobs.apply_view(deps, "KR")["state"] == "neutral"
    deps = _with(deps, agent_signal=False)
    assert "agent_signal" in jobs.apply_view(deps, "KR")["skipped"]


def test_regime_body_uses_foreign_flow_only_when_enabled(project, settings, conn):
    """Phase 21: hedge.flow_signal=true ∧ KR 이면 지수 proxy 외인 수급을 보조 지표로 읽는다.
    꺼져 있으면 KIS 수급 조회를 하지 않는다."""
    deps = _hedge_deps(project, settings, closes=BEAR)
    _mc(deps).flows = {"069500": [SimpleNamespace(foreign_net=-5)] * 20}
    out = jobs.regime_update(deps, "KR")
    assert out["signals"]["flow_weak"] is False and "get_investor_kr" not in _mc(deps).calls
    deps = replace(
        deps,
        settings=deps.settings.model_copy(
            update={"hedge": CFG.model_copy(update={"flow_signal": True})}
        ),
    )
    out = jobs.regime_update(deps, "KR")
    assert out["signals"]["flow_weak"] is True and out["count"] == 3
    assert "get_investor_kr" in _mc(deps).calls
    # 조회 실패는 False(지표 없음), 판정은 계속
    _mc(deps).fail = {"get_investor_kr"}
    out = jobs.regime_update(deps, "KR")
    assert out["signals"]["flow_weak"] is False


def test_proxy_candles_drop_today_bar_while_session_open():
    """2-P4(L7): 장중의 오늘 봉은 거래량이 있어도 미완성 — 국면 지표는 D−1 완성 봉까지만."""
    D_ = Decimal
    y = Candle(date=date(2026, 10, 22), open=D_(1), high=D_(1), low=D_(1), close=D_(100), volume=9)
    t = Candle(date=date(2026, 10, 23), open=D_(1), high=D_(1), low=D_(1), close=D_(90), volume=9)
    mc = SimpleNamespace(get_daily_kr=lambda *a, **k: [y, t])
    open_now = datetime(2026, 10, 23, 1, 0, tzinfo=UTC)  # 목 10:00 KST 장중
    assert inputs.proxy_candles(mc, "KR", CFG, now=open_now) == [y]
    closed = datetime(2026, 10, 23, 7, 0, tzinfo=UTC)  # 16:00 KST 마감 뒤
    assert inputs.proxy_candles(mc, "KR", CFG, now=closed) == [y, t]


def test_ksd_body_keeps_last_success_and_notifies_when_stale(project, settings, conn):
    """2-O9: 종류 하나의 수집이 실패하면 그 종류의 지난 일정을 유지하고, 3일 넘게 실패가 이어지면
    [권리 일정 수집 실패] 알림 — 조용히 빈 일정으로 블랙아웃이 꺼지지 않게."""

    class M:
        def __init__(self, fail=()):
            self.fail = set(fail)

        def ksd_schedule(self, kind, start, end):
            if kind in self.fail:
                raise RuntimeError("x")
            return [{"ticker": "005930", "kind": kind, "date": "2026-10-30", "name": "삼성전자"}]

    deps = _hedge_deps(project, settings, closes=[100] * 30)
    blk = BlackoutCfg(enabled=True, kinds=["paidin", "rev_split"])
    deps = replace(deps, settings=deps.settings.model_copy(update={"event_blackout": blk}))
    deps.ports["accounts"].market = M()
    assert jobs.ksd_body(deps, None)["events"] == 2
    assert deps.notifier.msgs == []
    deps.ports["accounts"].market = M(fail={"rev_split"})
    deps = replace(deps, now=lambda: NOW + timedelta(days=4))
    out = jobs.ksd_body(deps, None)
    assert out["events"] == 2 and out["stale"] == ["rev_split"]
    data = control.get_flag(conn, blackout.FLAG)
    assert sorted(e["kind"] for e in data["events"]) == ["paidin", "rev_split"]
    assert any("[권리 일정 수집 실패]" in m and "액면교체" in m for m in deps.notifier.msgs)


def test_ksd_body_collects_when_blackout_off_without_alert(project, settings, conn):
    """Ruling P7: 블랙아웃이 꺼져 있어도 KR 권리 일정을 모아 두고(켜는 날 바로 쓰게), 오래된
    종류가 있어도 꺼진 기능이라 알림은 보내지 않는다."""

    class M:
        def ksd_schedule(self, kind, start, end):
            if kind == "rev_split":
                raise RuntimeError("x")
            return [{"ticker": "005930", "kind": kind, "date": "2026-10-30", "name": "삼성전자"}]

    deps = _hedge_deps(project, settings, closes=[100] * 30)
    blk = BlackoutCfg(enabled=False, kinds=["paidin", "rev_split"])
    deps = replace(deps, settings=deps.settings.model_copy(update={"event_blackout": blk}))
    deps.ports["accounts"].market = M()
    out = jobs.ksd_body(deps, None)
    assert out["events"] == 1 and out["stale"] == ["rev_split"]
    assert [e["kind"] for e in control.get_flag(conn, blackout.FLAG)["events"]] == ["paidin"]
    assert deps.notifier.msgs == []


# --- 신규 -----------------------------------------------------------------------------


def test_regime_job_keeps_state_when_market_view_fails(project, settings, conn, monkeypatch):
    """Review Focus 3: 시장 세션이 예외·상한으로 제안이 없어도 국면 갱신은 끝나 있고, 재판정은
    전략가 신호 없이 같은 상태를 유지한다(세션 실패가 국면을 지우지 않음)."""
    deps = _hedge_deps(project, settings, closes=BEAR)
    deps = replace(
        deps,
        settings=deps.settings.model_copy(
            update={"hedge": CFG.model_copy(update={"agent_signal": True})}
        ),
    )

    def boom(d, m):
        raise RuntimeError("세션 폭발")

    monkeypatch.setattr(jobs, "market_view_body", boom)
    out = jobs.regime_job(deps, SimpleNamespace(market="KR"))
    assert out["regime"]["state"] == "bear" and "error" in out["view"]
    assert out["apply"]["state"] == "bear" and out["apply"]["agent_risk_off"] is False
    assert regime.load(conn, "KR")["state"] == "bear"
    assert any(m.startswith("[시장 세션 실패]") for m in deps.notifier.msgs)
    # 상한으로 제안 없음(view=None)도 같다
    capped = {"skipped": "세션 상한", "view": None}
    monkeypatch.setattr(jobs, "market_view_body", lambda d, m: capped)
    out = jobs.regime_job(deps, SimpleNamespace(market="KR"))
    assert out["apply"]["state"] == "bear" and regime.load(conn, "KR")["state"] == "bear"


def test_market_view_body_lane_context_uses_deps_state_secrets_and_cancel(
    project, settings, conn, monkeypatch
):
    """Task 5 리뷰 Minor 4: LaneContext.state 는 deps.state(= paths.state_dir()), 거시 키는
    ext["secrets"], 데몬 종료 이벤트가 세션 cancel 로 간다."""
    from trader.kernel.platform import paths

    deps = _hedge_deps(project, settings, closes=[100] * 36)
    seen = {}

    def fake_run(ctx, **kw):
        seen.update(ctx=ctx, **kw)
        return {"view": None}

    monkeypatch.setattr(mv, "run_market_view", fake_run)
    jobs.market_view_body(deps, "KR")
    assert seen["ctx"].state == deps.state == paths.state_dir()
    assert seen["ctx"].ext["secrets"] is deps.secrets
    assert seen["cancel"] is deps.stop_event and seen["cap_hit"] is False
    deps.stop_event.set()
    assert jobs.market_view_body(deps, "KR") == {"skipped": "종료 중"}


def test_jobs_build_registers_regime_and_ksd_ids(project, settings):
    schedule = load("schedule", build_registry(discover()), path=project / "config" / "x.yaml")
    ctx = SimpleNamespace(gates={"market": lambda job: None})

    def built(s, markets=("KR", "US")):
        out = daemon_jobs.build(
            [FEATURE], schedule=schedule, settings=s, markets=list(markets), ctx=ctx
        )
        return {j.id: j for j in out}

    # event_blackout 기본 꺼짐이어도 KR 이 있으면 ksd_events 를 등록한다(Ruling P7, 옛 daemon:401)
    assert settings.event_blackout.enabled is False
    got = built(settings)
    assert set(got) == {"kr_regime", "us_regime", "ksd_events"}
    kr, us = got["kr_regime"], got["us_regime"]
    assert (kr.market, kr.cron, kr.spec.gate) == (
        "KR", {"hour": 8, "minute": 25, "day_of_week": "mon-fri"}, "market",
    )  # fmt: skip
    # Ruling P6: 시장 세션(LLM)을 띄우므로 heavy 락 아래서 돈다
    assert (kr.spec.heavy, kr.spec.misfire, kr.spec.catch_up) == (True, "idempotent", "direct")
    assert str(us.trigger_kwargs["timezone"]) == "America/New_York" and us.cron["hour"] == 9
    assert got["ksd_events"].cron == {"hour": 7, "minute": 5, "day_of_week": "mon-fri"}
    assert (got["ksd_events"].spec.gate, got["ksd_events"].spec.heavy) == ("none", False)
    # US 만 쓰면 KR 예탁원 수집을 등록하지 않는다
    us_only = settings.model_copy(update={"markets": ["US"]})
    assert set(built(us_only, ["US"])) == {"us_regime"}
    off = settings.model_copy(update={"hedge": HedgeCfg(enabled=False)})
    assert "kr_regime" not in built(off)
