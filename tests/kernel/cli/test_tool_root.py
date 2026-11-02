import json

import pytest
import typer
from typer.testing import CliRunner

from trader.kernel.cli import tool_cmd
from trader.kernel.cli.main import build_app
from trader.kernel.registry.types import Feature, RegistryError, ToolCommand


def _cmd():
    tool_cmd.emit({"ok": True, "v": "한글"})


def test_tool_mounts_feature_tools_as_group_and_command():
    grp = typer.Typer()
    grp.command("sub")(_cmd)
    f = Feature(
        id="x", tier="service",
        tools=(ToolCommand("one", _cmd), ToolCommand("grp", grp)),
    )  # fmt: skip
    app = build_app([f])
    r = CliRunner().invoke(app, ["tool", "one"])
    assert r.exit_code == 0 and json.loads(r.stdout) == {"ok": True, "v": "한글"}
    r = CliRunner().invoke(app, ["tool", "grp", "sub"])
    assert r.exit_code == 0 and json.loads(r.stdout)["ok"] is True
    assert CliRunner().invoke(build_app([]), ["tool", "--help"]).exit_code == 0
    dup = Feature(id="y", tier="service", tools=(ToolCommand("one", _cmd),))
    with pytest.raises(RegistryError, match="one"):
        tool_cmd.build_tool_app([f, dup])


def test_emit_writes_single_json_line_and_exit_1_on_not_ok(capsys):
    tool_cmd.emit({"ok": True, "m": "가"})
    assert capsys.readouterr().out == '{"ok": true, "m": "가"}\n'
    with pytest.raises(typer.Exit) as ei:
        tool_cmd.emit({"ok": False, "error": "x"})
    assert ei.value.exit_code == 1
    assert json.loads(capsys.readouterr().out) == {"ok": False, "error": "x"}


def test_run_tool_wraps_exception(capsys):
    tool_cmd.run_tool(lambda: {"ok": True})
    assert json.loads(capsys.readouterr().out) == {"ok": True}

    def boom():
        raise KeyError("k")

    with pytest.raises(typer.Exit):
        tool_cmd.run_tool(boom)
    assert json.loads(capsys.readouterr().out) == {"ok": False, "error": "KeyError: 'k'"}
