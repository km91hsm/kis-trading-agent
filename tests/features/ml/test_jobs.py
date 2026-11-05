"""ml 잡·큐 kind·부트스트랩 훅 (옛 test_daemon/test_daemon_queue/test_daemon_stock_lessons 이식)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from tests.kernel.daemon.test_runner import make_deps
from trader.features.ingest import api as ingest_api
from trader.features.ml import features, universe_pit
from trader.features.ml import jobs as ml_jobs
from trader.features.ml.feature import FEATURE
from trader.kernel.cli.boot import build_registry
from trader.kernel.config.loader import load
from trader.kernel.daemon import jobs as daemon_jobs
from trader.kernel.daemon import queue as jobq
from trader.kernel.daemon.queue_jobs import QueueState
from trader.kernel.registry import discover

NOW = datetime(2026, 10, 10, 1, 0, tzinfo=UTC)


def _kinds():
    return QueueState(jobq.kinds_of(discover()))


class Sent:
    def __init__(self):
        self.sent = []

    def send(self, text, **kw):
        self.sent.append(text)
        return True


def _deps(project, settings, *, client=None, accounts=(), now=NOW, **kw):
    port = SimpleNamespace(
        accounts=lambda: list(accounts), brokers=lambda: [],
        market_client=lambda: client, holiday_fn=lambda: None,
    )  # fmt: skip
    return make_deps(
        project, settings=settings, now=lambda: now, notifier=Sent(), ports={"accounts": port},
        queue=_kinds(), **kw,
    )  # fmt: skip


def test_ml_train_body_calls_train(project, settings, monkeypatch):
    calls = []
    monkeypatch.setattr(
        ml_jobs, "train_quant", lambda cfg, **kw: calls.append(kw) or {"status": "trained"}
    )
    deps = _deps(project, settings)
    r = ml_jobs.ml_train_body(deps, None)
    assert r["status"] == "trained" and calls and calls[0]["state"] == deps.state
    assert calls[0]["market"] is None
    assert "rl_sizer" not in r  # RL 은 M8
    assert deps.notifier.sent and "퀀트" in deps.notifier.sent[0]


def test_ml_train_request_passes_market(project, settings, monkeypatch):
    calls = []
    monkeypatch.setattr(
        ml_jobs, "train_quant", lambda cfg, **kw: calls.append(kw) or {"status": "trained"}
    )
    ml_jobs.ml_train_request(_deps(project, settings), {"market": "KR"}, lambda s: None)
    assert calls[0]["market"] == "KR"


def test_ml_train_body_wraps_exceptions(project, settings, monkeypatch):
    def boom(cfg, **kw):
        raise RuntimeError("학습 폭발")

    monkeypatch.setattr(ml_jobs, "train_quant", boom)
    deps = _deps(project, settings)
    r = ml_jobs.ml_train_body(deps)
    assert r["status"] == "error" and any("학습 폭발" in m for m in deps.notifier.sent)


def test_build_jobs_registers_ml_train_sat_and_universe_members(project, settings):
    schedule = load("schedule", build_registry(discover()), path=project / "config" / "x.yaml")
    out = daemon_jobs.build(
        [FEATURE], schedule=schedule, settings=settings, markets=["KR", "US"],
        ctx=SimpleNamespace(gates={}),
    )  # fmt: skip
    got = {j.id: j for j in out}
    assert set(got) == {"ml_train", "universe_members", "features_snapshot"}
    assert got["ml_train"].cron == {
        "hour": 10, "minute": 0, "day_of_week": "sat",
    }  # fmt: skip
    assert str(got["ml_train"].trigger_kwargs["timezone"]) == "Asia/Seoul"
    assert got["universe_members"].cron == {"hour": 6, "minute": 40}
    assert got["features_snapshot"].cron == {"hour": 6, "minute": 30}  # 매일(day_of_week 없음)
    assert str(got["features_snapshot"].trigger_kwargs["timezone"]) == "Asia/Seoul"
    assert (got["ml_train"].spec.heavy, got["ml_train"].spec.catch_up) == (True, "none")
    assert (got["universe_members"].spec.heavy, got["universe_members"].spec.catch_up) == (
        False, "direct",
    )  # fmt: skip


def _universe(project, text):
    (project / "config").mkdir(exist_ok=True)
    (project / "config" / "universe.yaml").write_text(text, encoding="utf-8")


def test_universe_members_job_records(project, settings):
    _universe(
        project,
        "kr: ['005930', '000660', '114800']\nus:\n  - {symbol: AAPL, exchange: NAS}\n"
        "exclude: ['000660']\n",
    )
    deps = _deps(project, settings)
    out = ml_jobs.universe_members_body(deps, None)
    conn = deps.conn_factory()
    kr = set(universe_pit.members(conn, "KR")["ticker"])
    assert "005930" in kr and "000660" not in kr and "114800" not in kr  # 제외·헤지 ETF 빠짐
    assert out["KR"] == len(kr)


def test_universe_members_records_next_day_after_close_and_excludes_hedge(
    project, settings, conn
):
    _universe(project, "kr: ['005930', '114800']\n")
    after_close = datetime(2026, 10, 27, 7, 0, tzinfo=UTC)  # 16:00 KST, 정규장(15:30) 뒤
    deps = _deps(project, settings, now=after_close)
    ml_jobs.universe_members_body(deps, None)
    conn = deps.conn_factory()
    rows = conn.execute("SELECT ticker, first_seen FROM universe_members WHERE market='KR'")
    got = {r[0]: r[1] for r in rows}
    assert got == {"005930": "2026-10-28"}  # 114800(헤지)은 제외


def test_universe_members_includes_held_tickers(project, settings):
    from trader.kernel.execution import ledger as xledger

    _universe(project, "kr: ['005930']\n")
    acct = SimpleNamespace(id="paper-main", markets=["KR"])
    deps = _deps(project, settings, accounts=[acct])
    orig = xledger.latest_positions
    xledger.latest_positions = lambda c, a, m: {"positions": [{"ticker": "035720"}]}
    try:
        ml_jobs.universe_members_body(deps, None)
    finally:
        xledger.latest_positions = orig
    conn = deps.conn_factory()
    assert set(universe_pit.members(conn, "KR")["ticker"]) == {"005930", "035720"}


def test_features_backfill_request_reports_progress(project, settings, monkeypatch):
    seen = {}

    def fake_backfill(**kw):
        seen.update(kw)
        for i in range(3):
            kw["progress"](f"KR 00593{i}: 10행")
        return 30

    monkeypatch.setattr(features, "backfill", fake_backfill)
    monkeypatch.setattr(ingest_api, "build_dart", lambda s: "dart")
    monkeypatch.setattr(ingest_api, "build_edgar", lambda src, s: "edgar")
    client = object()
    deps = _deps(project, settings, client=client)
    msgs = []
    kind = jobq.kinds_of(discover())["features_backfill"]
    out = kind.body(deps, {"years": 2, "market": "KR"}, msgs.append)
    assert out == {"rows": 30}
    assert seen["markets"] == ["KR"] and seen["years"] == 2 and seen["market_client"] is client
    assert seen["dart"] == "dart" and seen["edgar"] == "edgar"
    assert msgs == [f"KR 00593{i}: 10행" for i in range(3)]  # 종목마다 진행 보고


def test_features_action_reports_progress_per_ticker(project, settings, monkeypatch):
    def fake_rederive(*, progress):
        progress("a")
        progress("b")
        return 2

    monkeypatch.setattr(features, "rederive", fake_rederive)
    deps = _deps(project, settings, client=None)
    msgs = []
    # derive 는 시세 클라이언트 없이도 돈다
    assert ml_jobs.features_action(deps, "derive", {}, msgs.append) == {"rows": 2}
    assert msgs == ["a", "b"]
    assert ml_jobs.features_action(deps, "snapshot", {}, msgs.append) == {
        "error": "시세 클라이언트 없음(피처 작업 불가)"
    }


def test_features_snapshot_body_calls_snapshot_action(project, settings, monkeypatch):
    calls = []

    def fake_action(deps, action, p, progress):
        calls.append((action, p))
        progress("x")
        return {"rows": 7}

    monkeypatch.setattr(ml_jobs, "features_action", fake_action)
    deps = _deps(project, settings, client=object())
    assert ml_jobs.features_snapshot_body(deps, None) == {"rows": 7}
    assert calls == [("snapshot", {})]


def test_features_snapshot_body_covers_all_markets(project, settings, monkeypatch):
    seen = {}

    def fake_snapshot(**kw):
        seen.update(kw)
        return 3

    monkeypatch.setattr(features, "snapshot", fake_snapshot)
    monkeypatch.setattr(ingest_api, "build_dart", lambda s: "dart")
    monkeypatch.setattr(ingest_api, "build_edgar", lambda *a: "edgar")
    monkeypatch.setattr(ingest_api, "load_sources", lambda r: None)
    monkeypatch.setattr(ingest_api, "load_universe", lambda: "uni")
    deps = _deps(project, settings, client=object())
    assert ml_jobs.features_snapshot_body(deps, None) == {"rows": 3}
    assert seen["markets"] == list(deps.settings.markets)


def test_queue_kinds_declaration():
    ks = {q.kind: q for q in ml_jobs.QUEUE_KINDS}
    assert list(ks) == ["features_backfill", "features_snapshot", "features_derive", "ml_train"]
    for q in ks.values():
        assert (q.lane, q.priority, q.trading, q.restart_requeue) == ("slow", 0, False, False)
    with pytest.raises(ValueError):
        ks["features_backfill"].params_model(years=9)


@pytest.mark.parametrize(
    "has_files,market,recent_fail,expected",
    [
        (False, True, False, ["features_backfill", "ml_train"]),
        (True, True, False, []),
        (False, False, False, []),
        (False, True, True, []),
    ],
)
def test_bootstrap(project, settings, has_files, market, recent_fail, expected):
    deps = _deps(project, settings, client=object() if market else None)
    if has_files:
        d = project / "state" / "features"
        d.mkdir(parents=True)
        (d / "2026-10-23.parquet").write_bytes(b"x")
    if recent_fail:
        conn = deps.conn_factory()
        kinds = deps.queue.kinds
        rid = jobq.enqueue(conn, kinds, "features_backfill", {"years": 3}, source="daemon", now=NOW)
        jobq.claim_next(conn, now=NOW, kinds=["features_backfill"])
        jobq.finish(conn, rid, "error", error="x", now=NOW - timedelta(hours=1))
    r = ml_jobs.bootstrap(deps=deps, point="daemon.bootstrap")
    assert r["queued"] == expected


def test_bootstrap_without_accounts_port_is_noop(project, settings):
    deps = make_deps(project, settings=settings, ports={}, queue=_kinds())
    assert ml_jobs.bootstrap(deps=deps) == {"queued": []}
