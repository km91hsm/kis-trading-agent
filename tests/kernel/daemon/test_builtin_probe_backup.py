"""내장 잡 usage_probe·ledger_backup."""

import sqlite3
from types import SimpleNamespace

from tests.kernel.daemon.test_runner import make_deps
from trader.kernel.config import LlmSettings
from trader.kernel.daemon import builtin_jobs as bj
from trader.kernel.session import usage


def _settings(**probe):
    llm = LlmSettings()
    for k, v in probe.items():
        setattr(llm.usage_probe, k, v)
    return SimpleNamespace(llm=llm, models={}, claude_bin=None)


def test_builtin_jobs_include_probe_and_backup():
    by_id = {j.id: j for j in bj.BUILTIN_JOBS}
    assert set(by_id) == {"telegram_poll", "usage_probe", "ledger_backup", "ops_check", "retention"}
    assert by_id["ops_check"].schedule_key == "ops_check.interval_s"
    assert by_id["usage_probe"].interval_s == 1800
    assert by_id["ledger_backup"].schedule_key == "ledger_backup"
    assert by_id["ledger_backup"].trigger == "cron"


def test_usage_probe_skips_when_recent_observation(project, monkeypatch):
    deps = make_deps(project, settings=_settings())
    monkeypatch.setattr(usage, "latest_observation", lambda *a, **k: {"age_s": 60})
    out = bj.usage_probe_body(deps)
    assert out["skipped"].startswith("최근 관측")


def test_usage_probe_disabled_skips(project):
    deps = make_deps(project, settings=_settings(enabled=False))
    assert bj.usage_probe_body(deps) == {"skipped": "종료 중·꺼짐"}


def test_ledger_backup_body_writes_file_and_prunes(project):
    deps = make_deps(project)
    state = deps.state
    # make_deps 의 DB 는 t.db — 백업은 기본 파일명 trader.db 를 읽는다
    src = sqlite3.connect(state / "t.db")
    dst = sqlite3.connect(state / "trader.db")
    src.backup(dst)
    src.close()
    dst.close()
    out = bj.ledger_backup_body(deps)
    assert out["file"].startswith("trader-") and out["pruned"] == 0
    assert (state / "backups" / out["file"]).exists()


def test_usage_probe_queue_kind_shared_by_daemon_cli_web(project, monkeypatch):
    """`usage_probe` 는 커널 내장 큐 kind — 데몬 큐 표·`trader jobs`·웹이 같은 목록(with_kernel)."""
    from typer.testing import CliRunner

    from trader.kernel.cli.jobs_cmd import build_jobs_app
    from trader.kernel.daemon import Daemon
    from trader.kernel.daemon.catalog import with_kernel
    from trader.kernel.daemon.queue import kinds_of

    q = kinds_of(with_kernel([]))["usage_probe"]
    assert q.lane == "info" and q.trading is False and q.restart_requeue is False
    daemon = Daemon([], make_deps(project, settings=_settings()))
    assert daemon.deps.queue.kinds["usage_probe"] is q
    assert "usage_probe" in CliRunner().invoke(build_jobs_app([]), ["kinds"]).output
    seen = {}
    monkeypatch.setattr(bj, "usage_probe_body", lambda d, force=False: seen.update(f=force) or {})
    assert q.body(None, {"force": True}, None) == {} and seen == {"f": True}


def test_usage_probe_interval_follows_settings():
    from trader.kernel.daemon.jobs import build
    from trader.kernel.registry.types import Feature

    feat = Feature(id="k", tier="lane", jobs=(bj.USAGE_PROBE,))
    for minutes in (30, 45):
        (job,) = build(
            [feat], schedule=SimpleNamespace(), settings=_settings(enabled=True,
            interval_min=minutes), markets=["KR"], ctx=SimpleNamespace(gates={}),
        )  # fmt: skip
        assert job.trigger_kwargs == {"seconds": minutes * 60}


def test_usage_probe_skips_when_statusline_file_is_recent(project, monkeypatch, tmp_path):
    """원장 관측이 없어도 대화형 상태줄 파일(`settings.llm.usage_file`)이 방금 갱신됐으면 확인
    호출을 하지 않는다(T18-R2: `latest_observation` 이 원장+파일을 합산)."""
    import json

    import pytest

    from tests.kernel.daemon.test_runner import NOW
    from trader.kernel.session import quick

    f = tmp_path / "rate_limits.json"
    f.write_text(
        json.dumps(
            {"at": int(NOW.timestamp()) - 60, "rate_limits": {"five_hour": {"used_percentage": 10}}}
        ),
        encoding="utf-8",
    )
    settings = _settings()
    settings.llm.usage_file = str(f)
    deps = make_deps(project, settings=settings)
    monkeypatch.setattr(quick, "probe_usage", lambda *a, **kw: pytest.fail("호출하면 안 됨"))
    out = bj.usage_probe_body(deps)
    assert out["skipped"] == "최근 관측 60초 전"


def test_usage_probe_backs_off_after_two_failures(project, monkeypatch):
    """사용량 확인이 연속 2번 실패(`agent_calls` usage_probe error)하면 PROBE_BACKOFF(2시간) 쉬고,
    실패는 잡 오류 대신 생략. force('지금 확인')는 쉬지 않는다."""
    from datetime import timedelta

    from tests.kernel.daemon.test_runner import NOW
    from trader.kernel.session import quick

    settings = _settings()
    settings.llm.usage_file = None  # 기본 상태줄 경로(홈)를 읽지 않게
    deps = make_deps(project, settings=settings)
    called = []
    monkeypatch.setattr(
        quick, "probe_usage", lambda *a, **kw: called.append(1) or {"error": "TimeoutExpired"}
    )
    out = bj.usage_probe_body(deps)
    assert "skipped" in out and "error" not in out and called == [1]
    conn = deps.conn_factory()
    for m in (40, 10):
        conn.execute(
            "INSERT INTO agent_calls(agent, model, status, error, created_at) "
            "VALUES ('usage_probe', 'haiku', 'error', 'TimeoutExpired', ?)",
            ((NOW - timedelta(minutes=m)).isoformat(),),
        )
    conn.commit()
    conn.close()
    assert bj.usage_probe_body(deps)["skipped"] == "연속 실패 — 2시간 쉼" and called == [1]
    bj.usage_probe_body(deps, force=True)
    assert called == [1, 1]
    # 마지막 실패가 PROBE_BACKOFF 밖이면 다시 확인한다
    later = make_deps(project, settings=settings, now=lambda: NOW + bj.PROBE_BACKOFF)
    bj.usage_probe_body(later)
    assert called == [1, 1, 1]
