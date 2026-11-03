"""`trader jobs list|run|kinds`: 요청만 넣고 실행은 데몬 (옛 tests/test_cli_jobs.py 이식).

실제 kind 대신 합성 Feature 의 QueueKind 를 `build_app` 에 넘긴다."""

import json
import os

from pydantic import BaseModel, ConfigDict
from typer.testing import CliRunner

from trader.kernel.cli.main import build_app
from trader.kernel.daemon import heartbeat
from trader.kernel.daemon import queue as jobq
from trader.kernel.ledger import connect
from trader.kernel.registry.types import Feature, QueueKind


class ScanParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market: str


class TickerParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market: str
    ticker: str
    exchange: str | None = None
    dry_run: bool = True


def _feature() -> Feature:
    body = lambda deps, params, progress: {}  # noqa: E731
    return Feature(
        id="synthetic", tier="lane",
        queue_kinds=(
            QueueKind("scan_x", ScanParams, "스캔 → 분석", "slow", 0, True, False, body),
            QueueKind("ticker_x", TickerParams, "종목 분석", "slow", 0, True, False, body),
            QueueKind("sync_x", BaseModel, "체결 동기화", "fast", 0, False, True, body),
        ),
    )  # fmt: skip


def _daemon_alive(project):
    (project / "state" / heartbeat.LOCK_NAME).write_text(
        json.dumps({"pid": os.getpid()}), encoding="utf-8"
    )


def _out(r) -> str:
    return r.stdout + (r.stderr or "")


def test_jobs_run_requires_daemon_then_enqueues(project):
    app = build_app([_feature()])
    runner = CliRunner()
    r = runner.invoke(app, ["jobs", "run", "scan_x", "--param", "market=KR"])
    assert r.exit_code == 1 and "데몬이 실행 중이어야 합니다" in _out(r)
    _daemon_alive(project)
    r = runner.invoke(app, ["jobs", "run", "scan_x", "--param", "market=KR"])
    assert r.exit_code == 0 and "스캔 → 분석" in r.stdout, r.stdout
    r = runner.invoke(app, ["jobs", "run", "scan_x", "--param", "market=KR"])
    assert r.exit_code == 1 and "이미 대기" in _out(r)
    r = runner.invoke(app, ["jobs", "run", "bogus"])
    assert r.exit_code == 1 and "알 수 없는 작업" in _out(r)
    r = runner.invoke(
        app,
        [
            "jobs", "run", "ticker_x", "--param", "market=US", "--param", "ticker=AAPL",
            "--param", "exchange=NAS", "--param", "dry_run=false", "--priority", "3",
        ],
    )  # fmt: skip
    assert r.exit_code == 0, _out(r)
    conn = connect()
    rows = jobq.list_recent(conn)
    conn.close()
    assert [(x["kind"], x["priority"], x["source"]) for x in rows] == [
        ("ticker_x", 3, "cli"),
        ("scan_x", 0, "cli"),
    ]
    assert rows[0]["params"] == {
        "market": "US",
        "ticker": "AAPL",
        "exchange": "NAS",
        "dry_run": False,
    }
    r = runner.invoke(app, ["jobs", "list"])
    assert r.exit_code == 0 and "#2 queued  p3" in r.stdout and "AAPL" in r.stdout
    r = runner.invoke(app, ["jobs", "kinds"])
    assert r.exit_code == 0 and "ticker_x" in r.stdout and "fast" in r.stdout
    assert "체결 동기화" in r.stdout


def test_jobs_list_empty(project):
    r = CliRunner().invoke(build_app([]), ["jobs", "list"])
    assert r.exit_code == 0 and "요청 없음" in r.stdout
