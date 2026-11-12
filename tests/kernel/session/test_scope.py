"""세션 범위(`kernel/session/scope.py`): 도구 허용·dry-run·배정 종목은 env 가 아니라 러너가 쓴
파일로 정한다."""

import json
import uuid

import pytest
import typer
from typer.testing import CliRunner

from trader.kernel.cli import tool_cmd
from trader.kernel.cli.main import build_app
from trader.kernel.registry.types import Feature, ToolCommand
from trader.kernel.session import scope


def _open(state, **over):
    sid = str(uuid.uuid4())
    kw = dict(kind="analyze", tools=["collect", "submit-decision"], dry_run=False, market="KR",
              tickers=["005930"], date=None)  # fmt: skip
    kw.update(over)
    scope.open_scope(state, sid, **kw)
    return sid


def test_no_session_env_means_no_scope_and_always_dry(tmp_path, monkeypatch):
    monkeypatch.delenv(scope.ENV_ID, raising=False)
    monkeypatch.setenv(scope.ENV_DRY_RUN, "0")
    assert scope.current(tmp_path) is None
    assert scope.tool_error("submit-market-view", None) is None  # 러너 밖: 도구 제한 없음
    assert scope.dry_run(tmp_path) is True  # 하지만 env 만으로는 주문이 나가지 않는다


def test_scope_file_decides_tools_dry_run_and_tickers(tmp_path, monkeypatch):
    sid = _open(tmp_path)
    monkeypatch.setenv(scope.ENV_ID, sid)
    monkeypatch.setenv(scope.ENV_DRY_RUN, "0")
    sc = scope.current(tmp_path)
    assert sc["kind"] == "analyze" and scope.dry_run(tmp_path) is False
    assert scope.tool_error("submit-decision", sc) is None
    assert "submit-market-view" in scope.tool_error("submit-market-view", sc)
    assert scope.ticker_error("005930", sc) is None
    assert "000660" in scope.ticker_error("000660", sc)
    monkeypatch.setenv(scope.ENV_DRY_RUN, "1")
    assert scope.dry_run(tmp_path) is True


def test_dry_session_cannot_be_made_live_by_env(tmp_path, monkeypatch):
    """dry-run 세션 안에서 LLM 이 명령 앞에 TRADER_DRY_RUN=0 을 붙여도 주문은 나가지 않는다."""
    monkeypatch.setenv(scope.ENV_ID, _open(tmp_path, dry_run=True))
    monkeypatch.setenv(scope.ENV_DRY_RUN, "0")
    assert scope.dry_run(tmp_path) is True


@pytest.mark.parametrize("sid", [str(uuid.uuid4()), "../../etc/passwd", "x"])
def test_unknown_or_forged_session_id_refuses_every_tool(tmp_path, monkeypatch, sid):
    monkeypatch.setenv(scope.ENV_ID, sid)
    monkeypatch.setenv(scope.ENV_DRY_RUN, "0")
    sc = scope.current(tmp_path)
    assert sc == scope.UNKNOWN and scope.tool_error("collect", sc)
    assert scope.dry_run(tmp_path) is True


def test_closed_scope_is_unknown(tmp_path, monkeypatch):
    sid = _open(tmp_path)
    monkeypatch.setenv(scope.ENV_ID, sid)
    scope.close_scope(tmp_path, sid)
    scope.close_scope(tmp_path, sid)  # 멱등
    assert scope.current(tmp_path) == scope.UNKNOWN


def test_session_without_assigned_tickers_is_not_ticker_limited(tmp_path, monkeypatch):
    monkeypatch.setenv(scope.ENV_ID, _open(tmp_path, tickers=[]))
    assert scope.ticker_error("000660", scope.current(tmp_path)) is None


def test_tool_root_refuses_commands_outside_session_scope(project, monkeypatch):
    """`trader tool <이름>` 진입점이 하위 명령을 돌리기 전에 세션 범위를 본다."""
    ran = []
    grp = typer.Typer()
    grp.command("sub")(lambda: (ran.append("grp"), tool_cmd.emit({"ok": True}))[1])
    f = Feature(
        id="x", tier="service",
        tools=(
            ToolCommand("one", lambda: (ran.append("one"), tool_cmd.emit({"ok": True}))[1]),
            ToolCommand("grp", grp),
        ),
    )  # fmt: skip
    app = build_app([f])
    state = project / "state"
    monkeypatch.setenv(scope.ENV_ID, _open(state, tools=["grp"]))
    r = CliRunner().invoke(app, ["tool", "one"])
    assert r.exit_code == 1 and ran == []
    out = json.loads(r.stdout)
    assert out["ok"] is False and "one" in out["error"]
    r = CliRunner().invoke(app, ["tool", "grp", "sub"])
    assert r.exit_code == 0 and ran == ["grp"]
    monkeypatch.delenv(scope.ENV_ID)  # 러너 밖에서는 제한 없음
    assert CliRunner().invoke(app, ["tool", "one"]).exit_code == 0 and ran == ["grp", "one"]


def test_args_error_binds_market_date_and_ticker_to_the_session(tmp_path, monkeypatch):
    """세션은 자기 시장·날짜·배정 종목에 대해서만 도구를 부를 수 있다 — KR 세션이 US 시장 판단을
    내거나, 종목 A 세션이 종목 B 의 체크포인트를 쓰거나, 과거 날짜 폴더에 결정을 내지 못한다."""
    from datetime import date

    sid = _open(tmp_path, date="2026-10-09")
    monkeypatch.setenv(scope.ENV_ID, sid)
    sc = scope.current(tmp_path)
    ok = {"ticker": "005930", "market": "KR", "date": date(2026, 10, 9), "stage": "10_x"}
    assert scope.args_error(ok, sc) is None
    assert scope.args_error({**ok, "date": "2026-10-09"}, sc) is None
    assert "000660" in scope.args_error({**ok, "ticker": "000660"}, sc)
    assert "US" in scope.args_error({**ok, "market": "US"}, sc)
    assert "2026-10-08" in scope.args_error({**ok, "date": date(2026, 10, 8)}, sc)
    assert scope.args_error({"market": "US"}, None) is None  # 러너 밖: 제한 없음
    assert scope.args_error(ok, scope.UNKNOWN)  # 범위를 모르면 거부


def test_run_tool_checks_args_before_running_the_tool(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(scope.ENV_ID, _open(tmp_path))
    monkeypatch.setattr(scope.paths, "state_dir", lambda: tmp_path)
    ran = []
    with pytest.raises(typer.Exit):
        tool_cmd.run_tool(lambda: ran.append(1) or {"ok": True}, args={"ticker": "000660"})
    assert ran == [] and "배정되지 않은 종목" in json.loads(capsys.readouterr().out)["error"]
    tool_cmd.run_tool(lambda: ran.append(1) or {"ok": True}, args={"ticker": "005930"})
    assert ran == [1]


def test_purge_stale_removes_only_old_scope_files(tmp_path):
    """데몬이 강제 종료되면 범위 파일이 남는다 — 기동 때 오래된 것을 지운다."""
    import os
    import time

    old, fresh = _open(tmp_path), _open(tmp_path)
    past = time.time() - 2 * 86400
    os.utime(tmp_path / scope.DIR_NAME / f"{old}.json", (past, past))
    assert scope.purge_stale(tmp_path) == 1
    left = [p.stem for p in (tmp_path / scope.DIR_NAME).glob("*.json")]
    assert left == [fresh]
