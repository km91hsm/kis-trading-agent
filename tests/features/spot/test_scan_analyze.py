"""spot `scan_analyze` 잡·`analyze_ticker` 큐 kind·`05_quant`(ports["ml"]).

옛 `tests/test_daemon.py` 11건과 `tests/test_daemon_queue.py::
test_analyze_ticker_request_runs_one_session_with_dry_run_precedence`(L128) 이식. 치환:
`daemon._analyze_body(deps, m)`→`scan_analyze.analyze_body`, `deps.session_runner`→
`scan_analyze.run_session` monkeypatch, `deps.input_deps`/`collect_and_save`→
`ports["inputs"].collect`,
`deps.features_fn("snapshot")`→`ports["ml"].snapshot`, `daemon._scan_body`→`ports["scan"].run_scan`.
옛 `_regime_body`·`_market_view_body`·`_shadow_record`(섀도 기록 테스트) 는 이식하지 않는다 — 국면은
별도 크론, 섀도는 M8. 신규: 훅 두 자리·크론 시각·`05_quant` 없어도 세션이 돈다(Review Focus 1)."""

import json
import logging
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from tests.kernel.daemon.test_runner import make_deps
from trader.features.spot import scan_analyze
from trader.features.spot.feature import FEATURE
from trader.kernel.cli.boot import build_registry
from trader.kernel.config.loader import load
from trader.kernel.daemon import jobs as daemon_jobs
from trader.kernel.daemon import queue as jobq
from trader.kernel.daemon.queue_jobs import QueueState, queue_tick
from trader.kernel.registry import discover
from trader.kernel.registry.types import Hook
from trader.kernel.session.run_store import market_dir, read_checkpoint, run_dir, write_checkpoint
from trader.kernel.session.runner import SessionResult, ThrottleBlocked

NOW = datetime(2026, 10, 8, 0, 0, tzinfo=UTC)  # 목요일 09:00 KST
TODAY = date(2026, 10, 8)
JOB = SimpleNamespace(market="KR")


class Sent:
    def __init__(self):
        self.msgs = []

    def send(self, text, *, escape=True):
        self.msgs.append(text)
        return True


class Inputs:
    def __init__(self, order=None):
        self.calls = []
        self.order = order

    def collect(self, **kw):
        self.calls.append(kw)
        if self.order is not None:
            self.order.append(f"collect:{kw['ticker']}")
        return {}


@pytest.fixture(autouse=True)
def _fake_claude(monkeypatch):
    """외부 FAKE_CLAUDE 값과 무관하게 같은 결과가 나오도록 FAKE 모드를 고정한다."""
    monkeypatch.setenv("FAKE_CLAUDE", "1")


def _deps(project, settings, *, ports=None, now=NOW, **kw):
    return make_deps(
        project, settings=settings, now=lambda: now, ports=ports if ports is not None else {},
        notifier=Sent(), queue=QueueState(kinds=jobq.kinds_of([FEATURE])), **kw,
    )  # fmt: skip


def _scan_file(project, targets, day=TODAY, market="KR"):
    write_checkpoint(
        market_dir(day, market, project / "state", create=True), "_scan",
        {"analyze_targets": targets},
    )  # fmt: skip


def _runner(monkeypatch, seen, fail=(), after=None):
    def runner(spec, kind, lane, **kw):
        t = spec.tickers[0]["ticker"]
        seen.append((t, spec, kw))
        if t in fail:
            raise RuntimeError("세션 실패")
        if after:
            after(t)
        return SessionResult(ok=True, session_id=f"s-{t}", cost_usd=1.0, fake=True)

    monkeypatch.setattr(scan_analyze, "run_session", runner)


def test_analyze_body_one_session_per_target_continues_on_failure(project, settings, monkeypatch):
    deps = _deps(project, settings)
    _scan_file(project, [{"ticker": t, "exchange": None} for t in "ABC"])
    seen = []
    _runner(monkeypatch, seen, fail={"B"})
    r = scan_analyze.analyze_body(deps, "KR")
    assert [t for t, *_ in seen] == ["A", "B", "C"]  # 종목당 세션 1회, 실패해도 계속
    assert r["ok"] == 2 and r["failed"] == ["B"]
    assert len(deps.notifier.msgs) == 1 and "분석 실패" in deps.notifier.msgs[0]


def test_analyze_body_without_scan_notifies_and_returns_error(project, settings, monkeypatch):
    """_scan.json 이 없으면(scan 잡 지연·실패) 조용히 ok 로 끝나지 않고 알림 + error 로 남긴다."""
    deps = _deps(project, settings)
    monkeypatch.setattr(
        scan_analyze, "run_session", lambda *a, **k: (_ for _ in ()).throw(AssertionError("금지"))
    )
    r = scan_analyze.analyze_body(deps, "KR")
    assert r["targets"] == 0 and "_scan" in r["error"]
    assert any("_scan" in m for m in deps.notifier.msgs)


def _scan_port(order=None, boom=None):
    def run_scan(lane, *, market, cancel=None, dry_run=False):
        if order is not None:
            order.append("scan")
        if boom is not None:
            raise boom
        return {"ok": 1}

    return SimpleNamespace(run_scan=run_scan, sector_of=lambda m: (lambda t: None))


def test_scan_analyze_body_runs_scan_then_analyze(project, settings, monkeypatch):
    order = []
    deps = _deps(project, settings, ports={"scan": _scan_port(order)})

    def analyze(d, m, **kw):
        order.append("analyze")
        return {"targets": 2}

    monkeypatch.setattr(scan_analyze, "analyze_body", analyze)
    r = scan_analyze.scan_analyze_body(deps, JOB)
    assert order == ["scan", "analyze"] and r["targets"] == 2


def test_scan_analyze_body_without_scan_port_returns_error(project, settings):
    assert scan_analyze.scan_analyze_body(_deps(project, settings), JOB) == {
        "error": "scan 포트 없음"
    }


def test_scan_analyze_body_scan_failure_still_runs_analyze_and_notifies(
    project, settings, monkeypatch
):
    """scan 이 예외로 죽어도 analyze 는 돈다 (보유 재분석은 _scan 없이는 못 하지만 analyze_body 가
    알림·error 를 남긴다)."""
    deps = _deps(project, settings, ports={"scan": _scan_port(boom=RuntimeError("screener 실패"))})
    called = []

    def analyze(d, m, **kw):
        called.append((m, kw["scan_error"]))
        return {"targets": 0, "error": "_scan 없음"}

    monkeypatch.setattr(scan_analyze, "analyze_body", analyze)
    r = scan_analyze.scan_analyze_body(deps, JOB)
    assert called == [("KR", "RuntimeError: screener 실패")] and r["error"]


def test_scan_analyze_job_closes_stale_running_before_sessions(project, settings, monkeypatch):
    """1시간 넘게 running 인 agent_calls 는 기동 때만이 아니라 매일 scan 앞에서도 정리한다."""
    deps = _deps(project, settings, ports={"scan": _scan_port()})
    c = deps.conn_factory()
    c.execute(
        "INSERT INTO agent_calls(agent, status, created_at) VALUES ('orchestrator','running',?)",
        ((NOW - timedelta(hours=2)).isoformat(),),
    )
    c.commit()
    monkeypatch.setattr(scan_analyze, "analyze_body", lambda d, m, **kw: {"targets": 0})
    scan_analyze.scan_analyze_body(deps, JOB)
    assert c.execute("SELECT status FROM agent_calls").fetchone()["status"] == "error"
    c.close()


def test_analyze_body_stops_launching_sessions_after_shutdown_signal(
    project, settings, monkeypatch
):
    """SIGTERM 뒤 남은 종목 세션을 계속 띄우면 ExitTimeOut 안에 못 끝난다 → 종료 플래그를 본다."""
    deps = _deps(project, settings)
    _scan_file(project, [{"ticker": t, "exchange": None} for t in "ABC"])
    seen = []
    _runner(monkeypatch, seen, after=lambda t: deps.stop_event.set())  # 첫 세션 도중 SIGTERM
    r = scan_analyze.analyze_body(deps, "KR")
    assert [t for t, *_ in seen] == ["A"] and r["ok"] == 1 and r.get("stopped") is True
    assert seen[0][2]["cancel"] is deps.stop_event


def test_scan_failure_is_reported_once_with_cause(project, settings):
    """scan 예외와 그로 인한 '_scan 없음' 이 알림 두 번·error 두 번이 되면 안 된다. 원인 하나."""
    deps = _deps(project, settings, ports={"scan": _scan_port(boom=ValueError("screener 폭발"))})
    r = scan_analyze.scan_analyze_body(deps, JOB)
    assert len(deps.notifier.msgs) == 1 and "screener 폭발" in deps.notifier.msgs[0]
    assert "screener 폭발" in r["error"]


def test_analyze_targets_collect_right_before_each_session(project, settings, monkeypatch):
    """수집은 종목 세션 **직전**에 — 전 종목을 먼저 모으면 마지막 종목 입력이 늙는다(리뷰 F26)."""
    order: list[str] = []
    deps = _deps(project, settings, ports={"inputs": Inputs(order)})
    _runner(monkeypatch, [], after=lambda t: order.append(f"session:{t}"))
    r = scan_analyze.analyze_targets(deps, "KR", [{"ticker": "A"}, {"ticker": "B"}], today=TODAY)
    assert r["ok"] == 2
    assert order == ["collect:A", "session:A", "collect:B", "session:B"]
    kw = deps.ports["inputs"].calls[0]
    assert (kw["exchange"], kw["force"], kw["conn_factory"]) == (None, False, deps.conn_factory)


def test_listed_discovery_snapshot_calls_ml_port_once(project, settings, monkeypatch, ml_port):
    """유니버스 밖 발굴 종목은 세션 앞에서 피처 스냅샷 1회(`ports["ml"].snapshot(tickers=)`)."""
    deps = _deps(project, settings, ports={"inputs": Inputs(), "ml": ml_port})
    _runner(monkeypatch, [])
    r = scan_analyze.analyze_targets(
        deps, "KR",
        [
            {"ticker": "005930"},
            {"ticker": "247540", "exchange": None, "discovery": "listed"},
            {"ticker": "900100", "exchange": None, "discovery": "listed"},
        ],
        today=TODAY,
    )  # fmt: skip
    assert r["ok"] == 3
    # 2-P9: 발굴 종목을 모아 스냅샷 1회(종목마다 스토어를 다시 읽지 않는다)
    assert ml_port.calls == [(["KR"], [("247540", None), ("900100", None)])]
    # ml 포트가 없으면 건너뛴다
    deps.ports["ml"] = None
    ml_port.calls.clear()
    scan_analyze.analyze_targets(
        deps, "KR", [{"ticker": "247540", "discovery": "listed"}], today=TODAY
    )
    assert ml_port.calls == []


def test_snapshot_listed_failure_only_warns(project, settings, ml_port, caplog):
    def boom(markets, *, tickers=None):
        raise RuntimeError("스토어 오류")

    ml_port.snapshot = boom
    deps = _deps(project, settings, ports={"ml": ml_port})
    with caplog.at_level(logging.WARNING, logger="trader.spot"):
        scan_analyze.snapshot_listed(deps, "KR", [{"ticker": "X", "exchange": None}])
    assert "피처 스냅샷 실패" in caplog.text


def test_analyze_body_skips_held_only_targets_by_horizon_profile(project, settings, monkeypatch):
    """Phase 23 T: 보유 종목(source=held)은 최근 결정의 프로필 주기(장기 5운영일)마다만 아침 재분석.
    picks·결정 없는 보유분은 그대로."""
    today = datetime(2026, 10, 27, 0, 0, tzinfo=UTC)  # 월요일
    deps = _deps(project, settings, now=today)
    c = deps.conn_factory()
    for ticker, hd in (("L", 60), ("S", 5), ("P", 60)):
        c.execute(
            "INSERT INTO decisions(date, market, ticker, action, data_json, created_at) "
            "VALUES ('2026-10-24','KR',?,'BUY',?,'2026-10-24T00:00:00+00:00')",
            (ticker, json.dumps({"horizon_days": hd})),
        )
    c.commit()
    c.close()
    _scan_file(
        project,
        [
            {"ticker": "P", "exchange": None, "source": "pick"},  # pick 은 항상
            {"ticker": "L", "exchange": None, "source": "held"},  # 장기, 1 평일 뒤 → 건너뜀
            {"ticker": "S", "exchange": None, "source": "held"},  # 단기(매일) → 분석
            {"ticker": "N", "exchange": None, "source": "held"},  # 결정 없음 → 분석
        ],
        day=date(2026, 10, 27),
    )
    seen = []
    _runner(monkeypatch, seen)
    r = scan_analyze.analyze_body(deps, "KR")
    assert [t for t, *_ in seen] == ["P", "S", "N"]
    assert [s["ticker"] for s in r["skipped_held"]] == ["L"]
    assert r["skipped_held"][0]["profile"] == "long"


def test_scan_analyze_throttled_skips_and_notifies_once_a_day(project, settings):
    now = datetime(2026, 10, 28, 22, 30, tzinfo=UTC)
    msg = "구독 5시간 사용률 85% ≥ 80% — 보유 재평가만 허용(screen 생략)"
    deps = _deps(project, settings, now=now, ports={"scan": _scan_port(boom=ThrottleBlocked(msg))})
    out = scan_analyze.scan_analyze_body(deps, JOB)
    assert "skipped" in out and "error" not in out and "사용량 감속" in out["skipped"]
    scan_analyze.scan_analyze_body(deps, JOB)
    assert len(deps.notifier.msgs) == 1 and "사용량 감속" in deps.notifier.msgs[0]


def test_analyze_ticker_request_runs_one_session_with_dry_run_precedence(
    project, settings, monkeypatch
):
    deps = _deps(project, settings, dry_run=True)
    seen = []
    _runner(monkeypatch, seen)
    c = deps.conn_factory()
    k = deps.queue.kinds
    jobq.enqueue(
        c, k, "analyze_ticker", {"market": "KR", "ticker": "005930", "dry_run": False},
        source="web", now=NOW,
    )  # fmt: skip
    queue_tick(deps, lanes=frozenset({"slow"}))
    t, spec, _ = seen[0]
    assert spec.tickers == [{"ticker": "005930", "exchange": None}]
    assert spec.dry_run is True  # 데몬 --dry-run 을 웹이 뒤집지 못한다
    req = jobq.list_recent(c)[0]
    assert req["status"] == "ok" and req["result"]["ok"] == 1
    # US 거래소는 원장(ticker_exchanges)으로 보강, 없으면 error
    c.execute(
        "INSERT INTO ticker_exchanges(market, ticker, exchange, updated_at) "
        f"VALUES ('US','AAPL','NAS','{NOW.isoformat()}')"
    )
    c.commit()
    jobq.enqueue(c, k, "analyze_ticker", {"market": "US", "ticker": "aapl"}, source="web", now=NOW)
    queue_tick(deps, lanes=frozenset({"slow"}))
    assert seen[1][1].tickers == [{"ticker": "AAPL", "exchange": "NAS"}]
    jobq.enqueue(c, k, "analyze_ticker", {"market": "US", "ticker": "ZZZZ"}, source="web", now=NOW)
    queue_tick(deps, lanes=frozenset({"slow"}))
    assert "거래소 코드" in jobq.list_recent(c)[0]["error"] and len(seen) == 2
    c.close()


def test_queue_kinds_declared():
    kinds = jobq.kinds_of([FEATURE])
    for k, label in (("scan_analyze", "스캔 → 분석"), ("analyze_ticker", "종목 분석")):
        q = kinds[k]
        assert (q.label, q.lane, q.priority, q.trading, q.restart_requeue) == (
            label, "slow", 0, True, False,
        )  # fmt: skip
    assert kinds["analyze_ticker"].params_model is scan_analyze.AnalyzeTickerParams


def test_scan_analyze_request_runs_market(project, settings, monkeypatch):
    deps = _deps(project, settings, ports={"scan": _scan_port()})
    monkeypatch.setattr(scan_analyze, "analyze_body", lambda d, m, **kw: {"targets": 1, "m": m})
    assert scan_analyze.scan_analyze_request(deps, {"market": "US"}, lambda s: None) == {
        "targets": 1, "m": "US",
    }  # fmt: skip


def test_scan_analyze_fires_pre_and_post_hooks(project, settings, monkeypatch):
    calls = []
    hooks = {
        "scan_analyze.pre": (
            Hook("scan_analyze.pre", lambda **kw: calls.append(("pre", kw["market"]))),
        ),
        "scan_analyze.post": (
            Hook("scan_analyze.post", lambda **kw: calls.append(("post", kw["result"]))),
        ),
    }  # fmt: skip
    deps = _deps(project, settings, ports={"scan": _scan_port(calls)}, hooks=hooks)
    monkeypatch.setattr(scan_analyze, "analyze_body", lambda d, m, **kw: {"targets": 0})
    scan_analyze.scan_analyze_body(deps, JOB)
    assert calls == [("pre", "KR"), "scan", ("post", {"targets": 0})]


def test_build_jobs_registers_scan_analyze_cron(project, settings):
    schedule = load("schedule", build_registry(discover()), path=project / "config" / "x.yaml")
    out = daemon_jobs.build(
        [FEATURE], schedule=schedule, settings=settings, markets=["KR", "US"],
        ctx=SimpleNamespace(gates={"market": lambda job: None, "monitor": lambda job: None}),
    )  # fmt: skip
    got = {j.id: j for j in out if j.id.endswith("scan_analyze")}
    assert set(got) == {"kr_scan_analyze", "us_scan_analyze"}
    kr, us = got["kr_scan_analyze"], got["us_scan_analyze"]
    assert kr.cron == {"hour": 7, "minute": 30, "day_of_week": "mon-fri"}
    assert str(kr.trigger_kwargs["timezone"]) == "Asia/Seoul"
    assert us.cron == {"hour": 8, "minute": 30, "day_of_week": "mon-fri"}
    assert str(us.trigger_kwargs["timezone"]) == "America/New_York"
    assert (kr.spec.heavy, kr.spec.gate, kr.spec.misfire, kr.spec.catch_up) == (
        True, "market", "scan", "queue",
    )  # fmt: skip
    # 국면 → scan → analyze 순서: regime 은 5분 앞
    from trader.features.regime.feature import FEATURE as REGIME

    reg = {
        j.id: j
        for j in daemon_jobs.build(
            [REGIME], schedule=schedule, settings=settings, markets=["KR", "US"],
            ctx=SimpleNamespace(gates={"market": lambda job: None, "monitor": lambda job: None}),
        )  # fmt: skip
    }
    assert (reg["kr_regime"].cron["hour"], reg["kr_regime"].cron["minute"]) == (7, 25)
    assert (reg["us_regime"].cron["hour"], reg["us_regime"].cron["minute"]) == (8, 25)


# --- Review Focus 1: 05_quant 는 선택 입력 --------------------------------------------------


def _quant_run(project, settings, monkeypatch, ports):
    deps = _deps(project, settings, ports={"inputs": Inputs(), **ports})
    seen = []
    _runner(monkeypatch, seen)
    r = scan_analyze.analyze_targets(deps, "KR", [{"ticker": "005930"}], today=TODAY)
    d = run_dir(TODAY, "KR", "005930", project / "state")
    return r, seen, read_checkpoint(d, "05_quant")


def test_analyze_targets_run_without_ml_port_or_model(
    project, settings, monkeypatch, ml_port, caplog
):
    r, seen, q = _quant_run(project, settings, monkeypatch, {})  # ml 포트 없음
    assert r["ok"] == 1 and len(seen) == 1 and q is None
    r, seen, q = _quant_run(project, settings, monkeypatch, {"ml": ml_port})  # 정상
    assert r["ok"] == 1 and q is not None and q["call"]["agent"] == "quant"
    d = run_dir(TODAY, "KR", "005930", project / "state")
    (d / "05_quant.json").unlink()
    ml_port.latest_model = lambda market: None  # 승격 모델 없음
    r, seen, q = _quant_run(project, settings, monkeypatch, {"ml": ml_port})
    assert r["ok"] == 1 and q is None
    off = SimpleNamespace(**{**vars(ml_port), "quant_enabled": lambda: False})
    r, seen, q = _quant_run(project, settings, monkeypatch, {"ml": off})
    assert r["ok"] == 1 and q is None

    def boom(market):
        raise RuntimeError("레지스트리 깨짐")

    ml_port.latest_model = boom
    with caplog.at_level(logging.WARNING, logger="trader.spot"):
        r, seen, q = _quant_run(project, settings, monkeypatch, {"ml": ml_port})
    assert r["ok"] == 1 and q is None and "05_quant 생략(레지스트리 오류)" in caplog.text


def test_analyze_targets_skips_when_throttled_without_collecting(project, settings, monkeypatch):
    monkeypatch.delenv("FAKE_CLAUDE", raising=False)  # 감속 선검사는 FAKE 가 아닐 때만 돈다
    monkeypatch.setattr(
        scan_analyze.usage, "session_allowance", lambda *a, **k: (False, "7일 84%")
    )
    inputs = Inputs()
    deps = _deps(project, settings, ports={"inputs": inputs})
    _runner(monkeypatch, [])
    r = scan_analyze.analyze_targets(deps, "KR", [{"ticker": "A"}], today=TODAY)
    assert "사용량 감속" in r["skipped"] and inputs.calls == []
    assert len(deps.notifier.msgs) == 1


def test_ensure_quant_failure_still_runs_calibration(project, settings, monkeypatch, caplog):
    """05_quant 실패가 35_calibration 을 건너뛰지 않고, 로그는 단계마다 따로 남는다."""
    seen = []

    def bad_quant(ctx, **kw):
        raise RuntimeError("퀀트 장애")

    def bad_cal(ctx, **kw):
        seen.append(kw["ticker"])
        raise RuntimeError("보정 장애")

    monkeypatch.setattr(scan_analyze.tools, "ensure_quant", bad_quant)
    monkeypatch.setattr(scan_analyze.tools, "ensure_calibration", bad_cal)
    with caplog.at_level(logging.WARNING, logger="trader.spot"):
        scan_analyze._ensure_quant(_deps(project, settings), "005930", "KR", TODAY)
    assert seen == ["005930"]
    assert "05_quant 생성 실패" in caplog.text and "35_calibration 생성 실패" in caplog.text
