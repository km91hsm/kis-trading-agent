"""Codex 자식은 발견된 npm 런타임만 보존하고 작업·비밀 환경은 상속하지 않는다."""

import os
import shutil
import sys

import pytest

from trader.kernel.platform import codex_bin
from trader.kernel.session.codex import child_env


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX nvm 실행 링크")
def test_nvm_symlink_keeps_discovered_node_in_child_path(tmp_path, monkeypatch):
    bin_dir = tmp_path / "nvm/versions/node/v22/bin"
    bin_dir.mkdir(parents=True)
    target = tmp_path / "nvm/versions/node/v22/lib/node_modules/@openai/codex/bin/codex.js"
    target.parent.mkdir(parents=True)
    target.write_text("#!/usr/bin/env node\n")
    target.chmod(0o755)
    (bin_dir / "codex").symlink_to(target)
    node = bin_dir / "node"
    node.write_text("synthetic runtime")
    node.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret")
    monkeypatch.setenv("TRADER_HOME", str(tmp_path / "private"))
    found = codex_bin.find_codex()
    assert shutil.which("node") == str(node)
    env = child_env(found)
    assert shutil.which("node", path=env["PATH"]) == str(node)
    assert "OPENAI_API_KEY" not in env and "TRADER_HOME" not in env


def test_windows_npm_shim_keeps_node_and_system_runtime_dirs(tmp_path, monkeypatch):
    npm = tmp_path / "npm"
    npm.mkdir()
    shim = npm / "codex.cmd"
    shim.write_text("synthetic shim")
    runtime = tmp_path / "nodejs"
    runtime.mkdir()
    system = tmp_path / "Windows"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SYSTEMROOT", str(system))
    monkeypatch.setattr(
        shutil, "which", lambda name: str(runtime / "node.exe") if name == "node" else str(shim)
    )
    found = codex_bin.find_codex()
    env = child_env(found)
    paths = env["PATH"].split(os.pathsep)
    assert str(runtime) in paths
    assert str(system / "System32") in paths
    assert str(npm) in paths


def test_windows_native_codex_is_still_discovered(tmp_path, monkeypatch):
    executable = tmp_path / "codex.exe"
    executable.write_text("synthetic native executable")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(shutil, "which", lambda name: str(executable) if name == "codex" else None)
    assert codex_bin.find_codex() == executable
