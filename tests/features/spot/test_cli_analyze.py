"""`trader spot analyze` — FAKE 세션 종단 테스트.

옛 `tests/test_cli_analyze.py` 에서 이식(`test_status_shows_last_decisions` 는 M12). 수집은
ingest 포트가 쓰는 `build_input_deps` 를 FakeMarket/FakeDart 로 바꿔 네트워크 없이 돌린다.
md 는 패키지 안에 있어 프로젝트로 복사하지 않는다."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import respx
import yaml
from typer.testing import CliRunner

from tests.features.ingest.helpers import FakeDart, FakeMarket
from trader.features.ingest import ports as ingest_ports
from trader.features.ingest.inputs import InputDeps
from trader.features.spot import analyze as analyze_mod
from trader.features.spot.feature import FEATURE
from trader.features.spot.session_kinds import ANALYZE
from trader.kernel.cli.main import app
from trader.kernel.config import KST
from trader.kernel.ledger import db
from trader.kernel.platform import bundle
from trader.kernel.session import agents as ag
from trader.kernel.session.run_store import read_checkpoint, run_dir
from trader.kernel.session.runner import SessionResult, SessionSpec, build_command

_ASOF_DT = datetime.now(UTC) - timedelta(hours=1)  # 24시간 창 안의 과거 시각
ASOF = _ASOF_DT.isoformat()
DAY = _ASOF_DT.astimezone(KST).date()
DAY_S = DAY.isoformat()


@pytest.fixture
def quiet_project(project, monkeypatch):
    """소스 전부 끈 sources.yaml · paper-main 합성 계좌 · 가짜 수집 의존성 · HTTP 차단."""
    (project / "config" / "sources.yaml").write_text(
        yaml.safe_dump(
            {
                "edgar": {"enabled": False, "user_agent": "trader test@example.com"},
                "news": {"enabled": False},
                "macro": {"enabled": False},
                "dart": {"enabled": False},
                "community": {"enabled": False},
            }
        ),
        encoding="utf-8",
    )
    (project / "config" / "accounts.yaml").write_text(
        yaml.safe_dump(
            {
                "accounts": [
                    {
                        "id": "paper-main",
                        "env": "paper",
                        "cano": "00000000",
                        "markets": ["KR", "US"],
                        "mode": "auto",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        ingest_ports,
        "build_input_deps",
        lambda *a, **k: InputDeps(market=FakeMarket(), dart=FakeDart(), edgar=None),
    )
    with respx.mock(assert_all_called=False):
        yield project


def _run(args):
    return CliRunner().invoke(app, ["spot", "analyze", *args])


def test_cli_analyze_fake_creates_decision(quiet_project, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    r = _run(["--ticker", "005930", "--asof", ASOF, "--dry-run"])
    assert r.exit_code == 0, r.output
    assert "결정: HOLD" in r.stdout and "30_decision.json" in r.stdout and "FAKE" in r.stdout
    d = quiet_project / "state" / "runs" / DAY_S / "KR" / "005930"
    assert (d / "30_decision.json").exists() and (d / "00_inputs.json").exists()
    conn = db.connect()
    assert conn.execute("SELECT status FROM runs").fetchone()["status"] == "ok"
    assert conn.execute("SELECT COUNT(*) FROM agent_calls").fetchone()[0] == 1


def test_rerun_only_missing_stages(quiet_project, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    assert _run(["--ticker", "005930", "--asof", ASOF, "--dry-run"]).exit_code == 0
    d = quiet_project / "state" / "runs" / DAY_S / "KR" / "005930"
    (d / "20_debate_r1_bear.json").unlink()
    (d / "30_decision.json").unlink()
    r = _run(["--ticker", "005930", "--asof", ASOF, "--dry-run"])
    assert r.exit_code == 0, r.output
    assert "새로 쓴 단계: 20_debate_r1_bear, 25_research_summary, 30_decision" in r.stdout


def test_cli_analyze_without_dry_run_records_nondry_verdict(quiet_project, monkeypatch):
    """--dry-run 없이 FAKE 세션: 판정이 dry_run=False 로 기록된다. 브로커(앱키)가 없으면
    잔고를 못 봐 판정 불가(fail-closed)이고 어떤 계좌에도 주문 기록이 없다(FAKE 는 HOLD)."""
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    r = _run(["--ticker", "005930", "--asof", ASOF])
    assert r.exit_code == 0, r.output
    d = run_dir(DAY, "KR", "005930")
    rc = read_checkpoint(d, "40_risk_check_paper-main")
    assert rc is not None and rc["data"]["dry_run"] is False
    assert rc["data"]["approved"] is False and "NO_BROKER" in rc["data"]["reasons"][0]
    assert read_checkpoint(d, "50_order_paper-main") is None


def test_cli_analyze_dry_run_writes_verdict_without_order(quiet_project, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    r = _run(["--ticker", "005930", "--asof", ASOF, "--dry-run"])
    assert r.exit_code == 0, r.output
    d = run_dir(DAY, "KR", "005930")
    assert read_checkpoint(d, "40_risk_check_paper-main")["data"]["dry_run"] is True
    assert read_checkpoint(d, "50_order_paper-main") is None


def test_cli_analyze_skip_collect_without_inputs_fails(quiet_project, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    r = _run(["--ticker", "005930", "--asof", ASOF, "--dry-run", "--skip-collect"])
    assert r.exit_code == 1 and "00_inputs" in r.output


def test_cli_analyze_session_failure_exit1(quiet_project, monkeypatch):
    monkeypatch.delenv("FAKE_CLAUDE", raising=False)
    monkeypatch.setattr(
        analyze_mod,
        "run_session",
        lambda *a, **k: SessionResult(ok=False, session_id="s", error="budget exceeded"),
    )
    r = _run(["--ticker", "005930", "--asof", ASOF, "--dry-run"])
    assert r.exit_code == 1 and "budget" in r.output


def test_cli_analyze_us_requires_exchange(quiet_project, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    r = _run(["--ticker", "AAPL", "--market", "US", "--asof", ASOF, "--dry-run"])
    assert r.exit_code == 1


def test_cli_analyze_us_without_exchange_creates_no_run_dir(quiet_project, monkeypatch):
    """Review Focus 5: 검사 전에 어떤 디렉터리도 만들지 않는다."""
    monkeypatch.setenv("FAKE_CLAUDE", "1")
    r = _run(["--ticker", "AAPL", "--market", "US", "--asof", ASOF, "--dry-run"])
    assert r.exit_code == 1 and "--exchange" in r.output
    assert not (quiet_project / "state" / "runs").exists() or not any(
        (quiet_project / "state" / "runs").iterdir()
    )


def test_allowed_tools_matches_tool_command_and_prompt(quiet_project, monkeypatch):
    """M0 계약: --allowedTools 값 = Bash(<tool_command> *), 프롬프트도 같은 명령을 쓴다."""
    from trader.kernel.cli.boot import boot

    b = boot(settings=True)
    spec = SessionSpec(market="KR", date=DAY, tickers=[{"ticker": "005930", "exchange": None}])
    agents = ag.by_name(FEATURE.agents)

    def make_prompt() -> str:
        return ag.orchestrator_prompt(ANALYZE, agents, debate_rounds=1, checkpoint_names=())

    cmd = build_command(
        spec, ANALYZE, b.settings, Path("/x/claude"), Path("/a.json"),
        session_id="u", prompt=make_prompt(),
    )  # fmt: skip
    pairs = {cmd[i]: cmd[i + 1] for i in range(2, len(cmd) - 1)}
    assert pairs["--allowedTools"] == f"Bash({bundle.tool_command()} *)"
    assert bundle.tool_command() in make_prompt()
    monkeypatch.setattr(bundle, "is_frozen", lambda: True)
    frozen = make_prompt()
    assert "trader tool status" in frozen and "python -m trader tool" not in frozen
    cmd = build_command(
        spec, ANALYZE, b.settings, Path("/x/claude"), Path("/a.json"),
        session_id="u", prompt=frozen,
    )  # fmt: skip
    assert "Bash(trader tool *)" in cmd
