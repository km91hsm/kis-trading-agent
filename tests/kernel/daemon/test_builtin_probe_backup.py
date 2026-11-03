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
    assert set(by_id) == {"telegram_poll", "usage_probe", "ledger_backup"}
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
