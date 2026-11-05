"""spot 데몬 ↔ 국면 헤지: monitor 틱의 헤지 1회(`hedge.hedge_tick`)·paused 보류·헤지 실패 격리.

옛 `tests/test_daemon_hedge.py` 2·3·4 이식 + 5번(`test_market_view_body_runs_fake_session_once…`)의
헤지 진입 단언(`_hedge_body` 가 제안 비중 10% 로 진입) 복원 — regime 잡 테스트는 spot 을 import
할 수 없어 여기로 옮겼다. 치환(플랜 「바꾸는 옛 단언」): `daemon._regime_body`→
`regime.jobs.regime_update`, `_market_view_body`→`regime.jobs.market_view_body`, `_hedge_body`→
`spot.hedge.hedge_tick`, `_monitor_body(deps, m)`→`spot.jobs.monitor_body(deps, job)`, `_deps`→
`DaemonDeps` + 합성 계좌 포트(`ports["accounts"]`)·regime 포트, `Settings(hedge=…)`→
`settings.model_copy`, `monitor_mod.monitor_once` monkeypatch 는 spot `monitor`. 신규
`test_hedge_tick_skips_quietly_without_regime_port`(Review Focus 1)."""

import threading
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from tests.features.spot.conftest import make_account
from trader.features.accounts.ports import AccountsPortImpl
from trader.features.accounts.settings import to_broker_account
from trader.features.regime import jobs as regime_jobs
from trader.features.regime import ports as regime_ports
from trader.features.regime.settings import HedgeCfg
from trader.features.spot import hedge
from trader.features.spot import jobs as spot_jobs
from trader.features.spot import monitor as monitor_mod
from trader.kernel.broker.fake import FakeBroker, FakeMarket
from trader.kernel.broker.models import Candle, Cash
from trader.kernel.config import Secrets
from trader.kernel.daemon.runner import DaemonDeps
from trader.kernel.ledger import control, db

NOW = datetime(2026, 10, 24, 1, 0, tzinfo=UTC)  # 10:00 KST (KR monitor 창 안)
CFG = HedgeCfg(ma_days=20, momentum_days=5, exit_streak_days=1)
BEAR = [100] * 30 + [95, 90, 85, 80, 75, 70]
JOB = SimpleNamespace(market="KR")


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


class _Port(AccountsPortImpl):
    """합성 계좌 + 가짜 브로커·시세(네트워크 없음)."""

    def __init__(self, accounts, brokers, market):
        super().__init__(accounts, Secrets())
        self._brokers, self.market = brokers, market

    def brokers(self):
        return list(self._brokers.values())

    def market_client(self):
        return self.market


def _hedge_deps(project, settings, *, closes, prices=None) -> DaemonDeps:
    market = FakeMarket(prices=prices or {}, candles={"069500": _candles(closes)}, now=lambda: NOW)
    acct = make_account("paper-main", "paper", "auto", markets=["KR", "US"])
    broker = FakeBroker(
        to_broker_account(acct),
        cash={"KR": Cash(currency="KRW", available=Decimal("10000000"), total=Decimal("10000000"))},
    )
    s = settings.model_copy(update={"hedge": CFG})
    ports = {
        "accounts": _Port([acct], {"paper-main": broker}, market),
        "regime": regime_ports.build_port(SimpleNamespace(settings=s)),
    }
    return DaemonDeps(
        settings=s,
        schedule=None,
        conn_factory=db.connect,
        state=project / "state",
        now=lambda: NOW,
        markets=["KR", "US"],
        gates={},
        stop_event=threading.Event(),
        dry_run=False,
        notifier=Notes(),
        secrets=Secrets(),
        ports=ports,
    )


def _broker(deps):
    return next(b for b in deps.ports["accounts"].brokers() if b.account.id == "paper-main")


def test_monitor_body_runs_hedge_once_per_day(project, settings, conn, monkeypatch):
    deps = _hedge_deps(project, settings, closes=BEAR, prices={"114800": Decimal("5000")})
    regime_jobs.regime_update(deps, "KR")
    monkeypatch.setattr(monitor_mod, "monitor_once", lambda ctx, **kw: {"checked": 0})
    out = spot_jobs.monitor_body(deps, JOB)
    assert out["hedge"]["action"] == "enter" and out["hedge"]["ticker"] == "114800"
    orders = _broker(deps).orders
    assert len(orders) == 1 and orders[0].ticker == "114800" and orders[0].qty == 400
    assert control.get_flag(conn, hedge.done_key("KR", date(2026, 10, 24)))
    out2 = spot_jobs.monitor_body(deps, JOB)
    assert "hedge" not in out2 and len(_broker(deps).orders) == 1


def test_monitor_body_hedge_paused_notifies_once(project, settings, conn, monkeypatch):
    deps = _hedge_deps(project, settings, closes=BEAR, prices={"114800": Decimal(1)})
    regime_jobs.regime_update(deps, "KR")
    control.set_flag(conn, "paused", True)
    monkeypatch.setattr(monitor_mod, "monitor_once", lambda ctx, **kw: {"checked": 0})
    spot_jobs.monitor_body(deps, JOB)
    spot_jobs.monitor_body(deps, JOB)
    assert sum("[헤지 보류: 일시정지]" in m for m in deps.notifier.msgs) == 1
    assert _broker(deps).orders == []


def test_hedge_failure_does_not_block_monitor(project, settings, conn, monkeypatch):
    deps = _hedge_deps(project, settings, closes=[100] * 30)
    monkeypatch.setattr(monitor_mod, "monitor_once", lambda ctx, **kw: {"checked": 7})

    def boom(d, m):
        raise RuntimeError("x")

    monkeypatch.setattr(hedge, "hedge_tick", boom)
    out = spot_jobs.monitor_body(deps, JOB)
    assert out["checked"] == 7 and out["hedge"]["error"].startswith("RuntimeError")
    assert any("[헤지 실패] KR" in m for m in deps.notifier.msgs)


def test_hedge_tick_skips_quietly_without_regime_port(project, settings, conn, monkeypatch):
    """Review Focus 1: regime 포트가 없거나 hedge.enabled=false 면 헤지 틱은 주문·알림 없이
    `{"skipped": …}` 로 끝나고 monitor 결과에 `hedge` 가 실리지 않는다(손절 점검은 그대로)."""
    deps = _hedge_deps(project, settings, closes=BEAR, prices={"114800": Decimal("5000")})
    regime_jobs.regime_update(deps, "KR")
    n = len(deps.notifier.msgs)
    no_port = replace(deps, ports={"accounts": deps.ports["accounts"]})
    assert hedge.hedge_tick(no_port, "KR") == {"skipped": "비활성"}
    monkeypatch.setattr(monitor_mod, "monitor_once", lambda ctx, **kw: {"checked": 1})
    out = spot_jobs.monitor_body(no_port, JOB)
    assert out["checked"] == 1 and "hedge" not in out and _broker(no_port).orders == []
    off = HedgeCfg(enabled=False)
    s_off = deps.settings.model_copy(update={"hedge": off})
    off_port = regime_ports.build_port(SimpleNamespace(settings=s_off))
    disabled = replace(deps, settings=s_off, ports={**deps.ports, "regime": off_port})
    assert hedge.hedge_tick(disabled, "KR") == {"skipped": "비활성"}
    assert _broker(deps).orders == [] and len(deps.notifier.msgs) == n


def test_market_view_then_hedge_tick_enters_at_proposal_weight(
    project, settings, conn, monkeypatch
):
    """옛 `test_market_view_body_runs_fake_session_once_and_notifies` 의 마지막 단언: 시장 세션
    제안(RISK_OFF 10%) 뒤 헤지는 제안 비중으로 진입한다."""
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    deps = _hedge_deps(project, settings, closes=BEAR)
    regime_jobs.regime_update(deps, "KR")
    out = regime_jobs.market_view_body(deps, "KR")
    assert out["view"]["stance"] == "RISK_OFF" and out["view"]["hedge_weight"] == 0.1
    # 그 뒤 헤지는 제안 비중(10%)으로 진입
    deps.ports["accounts"].market.prices = {"114800": Decimal("5000")}
    hedge_out = hedge.hedge_tick(deps, "KR")
    assert hedge_out["action"] == "enter" and hedge_out["weight"] == 0.1
