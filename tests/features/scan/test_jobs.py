"""scan `{market}_theme` 잡: 등록·기록·상위 테마가 바뀔 때만 알림·기록한 멤버 사용.

옛 `tests/test_daemon_theme.py` 1·2·5 이식(3건). 치환: `daemon.build_jobs`→`daemon_jobs.build`
(+ 실제 schedule), `daemon._theme_body(deps, "KR")`→`jobs.theme_body(deps, job)`(job.market),
`_deps`→`DaemonDeps` + `ports["accounts"]` 가짜 포트, `Settings(theme=…)`→`settings.model_copy`.
단언은 그대로. 3(api)·4(cli)는 각각 web·`test_cli.py` 로 간다."""

import threading
from datetime import UTC, date, datetime
from types import SimpleNamespace

from tests.kernel.daemon.conftest import FakeAccountsPort
from trader.features.scan import jobs, theme
from trader.features.scan.feature import FEATURE
from trader.features.scan.settings import ThemeCfg
from trader.kernel.broker.fake import FakeMarket
from trader.kernel.broker.models import RankRow
from trader.kernel.cli.boot import build_registry
from trader.kernel.config.loader import load
from trader.kernel.daemon import jobs as daemon_jobs
from trader.kernel.daemon.runner import DaemonDeps
from trader.kernel.ledger import control, db
from trader.kernel.registry import discover

NOW = datetime(2026, 10, 26, 2, 0, tzinfo=UTC)  # 월 11:00 KST
KR = SimpleNamespace(market="KR")


class Sent:
    def __init__(self):
        self.msgs = []

    def send(self, text, *, escape=True):
        self.msgs.append(text)
        return True


def _sectors(project):
    (project / "config" / "sectors.yaml").write_text(
        "sectors: [반도체, 2차전지, 기타]\nkr: {'111111': 반도체, '333333': 2차전지}\nus: {}\n",
        encoding="utf-8",
    )


def _deps(settings, mc) -> DaemonDeps:
    return DaemonDeps(
        settings=settings,
        schedule=None,
        conn_factory=db.connect,
        state=None,
        now=lambda: NOW,
        markets=["KR", "US"],
        gates={},
        stop_event=threading.Event(),
        dry_run=True,
        notifier=Sent(),
        ports={"accounts": FakeAccountsPort(market_client=mc)},
    )


def test_build_jobs_registers_theme_when_enabled(project, settings):
    registry = build_registry(discover())
    schedule = load("schedule", registry, path=project / "config" / "schedule.yaml")
    ctx = SimpleNamespace(gates={"monitor": lambda *a: None})

    def built(s, markets=("KR", "US")):
        out = daemon_jobs.build(
            [FEATURE], schedule=schedule, settings=s, markets=list(markets), ctx=ctx
        )
        return {j.id: j for j in out}

    got = built(settings)
    assert got["kr_theme"].kind == "theme" and got["kr_theme"].interval_s == 1800
    assert got["kr_theme"].spec.gate == "monitor" and not got["kr_theme"].spec.heavy
    sched = project / "config" / "schedule.yaml"
    sched.write_text("theme:\n  interval_s: 600\n", encoding="utf-8")
    custom = load("schedule", registry, path=sched)
    out = daemon_jobs.build(
        [FEATURE], schedule=custom, settings=settings, markets=["KR"], ctx=ctx
    )
    assert out[0].interval_s == 600  # schedule.theme.interval_s 를 읽는다
    off = built(settings.model_copy(update={"theme": ThemeCfg(enabled=False)}), ("KR",))
    assert not any(j.kind == "theme" for j in off.values())


def test_theme_job_alerts_only_on_top_change(conn, project, settings):
    _sectors(project)
    mc = FakeMarket(ranks={"volume": [RankRow(ticker="111111", rank=1)]})
    deps = _deps(settings, mc)
    out = jobs.theme_body(deps, KR)
    assert out["themes"] == 1 and out["alerted"] is True
    assert deps.notifier.msgs[-1].startswith("[테마 열기] KR\n1. 반도체(섹터)")
    n = len(deps.notifier.msgs)
    assert jobs.theme_body(deps, KR)["alerted"] is False  # 같은 상위 테마 → 알림 없음
    mc.ranks["volume"] = [RankRow(ticker="333333", rank=1)]
    assert jobs.theme_body(deps, KR)["alerted"] is True
    assert len(deps.notifier.msgs) == n + 1
    rows = conn.execute("SELECT theme, rank FROM theme_heat ORDER BY theme").fetchall()
    assert [r["theme"] for r in rows] == ["섹터:2차전지", "섹터:반도체"]
    assert control.get_flag(conn, "theme_heat:KR")["date"] == "2026-10-26"


def test_theme_map_uses_recorded_members(conn, project, settings):
    _sectors(project)
    deps = _deps(settings, FakeMarket(ranks={"volume": [RankRow(ticker="111111", rank=1)]}))
    jobs.theme_body(deps, KR)
    assert theme.theme_of_map(conn, "KR", date(2026, 10, 26), top_n=3) == {
        "111111": ["섹터:반도체"]
    }


def test_discover_request_runs_fake_scout_and_queues_candidates(conn, project, monkeypatch):
    """옛 `test_discover_body_runs_fake_scout_and_queues_candidates` 이식: 뉴스 트리거가 넣은
    discover 요청을 본문(`triggers.discover_request`)으로 실행한다."""
    from tests.features.scan.test_triggers import NOW as TNOW
    from tests.features.scan.test_triggers import _news, _queued, trigger_deps
    from trader.features.scan import triggers
    from trader.kernel.session.run_store import market_dir, read_checkpoint, run_date_for

    monkeypatch.setenv("FAKE_CLAUDE", "1")
    (project / "config" / "universe.yaml").write_text(
        "kr: ['005930', '000660']\n", encoding="utf-8"
    )
    deps = trigger_deps(project, conn, notifier=Sent())
    _news(conn, [("삼성전자 대형 수주", ["005930"])])
    uni = SimpleNamespace(kr=["005930", "000660"], us=[], watch=[], exclude=[])
    triggers.news_trigger(deps, conn, "KR", uni)
    # analyze_ticker(priority 5)가 먼저 잡히므로 취소하고 discover 만 실행
    conn.execute("UPDATE job_requests SET status='skipped' WHERE kind='analyze_ticker'")
    conn.commit()
    assert [k for k, _, _ in _queued(conn)] == ["discover"]
    res = triggers.discover_request(deps, {"market": "KR"}, lambda text: None)
    assert res["novel"] == 1 and res["candidates"] == 1
    assert res["queued"][0]["ticker"] == "005930" and res["queued"][0]["tier"] == "universe"
    d = market_dir(run_date_for("KR", TNOW), "KR", deps.state)
    assert read_checkpoint(d, "_discovery_r1")["data"]["candidates"][0]["ticker"] == "005930"


def test_discover_request_skips_without_novel_news(conn, project):
    from tests.features.scan.test_triggers import trigger_deps
    from trader.features.scan import triggers

    deps = trigger_deps(project, conn)
    assert triggers.discover_request(deps, {"market": "KR"}, lambda t: None) == {
        "skipped": "새 뉴스 없음"
    }


def test_discover_request_closes_stale_running_calls_first(conn, project):
    """발굴 요청 시작 시 1시간 넘게 running 인 agent_calls 행을 error 로 닫는다(새 뉴스 없어도)."""
    from datetime import timedelta

    from tests.features.scan.test_triggers import NOW as TNOW
    from tests.features.scan.test_triggers import trigger_deps
    from trader.features.scan import triggers

    for when in (TNOW - timedelta(hours=2), TNOW - timedelta(minutes=5)):
        conn.execute(
            "INSERT INTO agent_calls(agent, status, created_at) VALUES ('orchestrator', "
            "'running', ?)",
            (when.isoformat(),),
        )
    conn.commit()
    deps = trigger_deps(project, conn)
    triggers.discover_request(deps, {"market": "KR"}, lambda t: None)
    rows = conn.execute("SELECT status FROM agent_calls ORDER BY id").fetchall()
    assert [r["status"] for r in rows] == ["error", "running"]
