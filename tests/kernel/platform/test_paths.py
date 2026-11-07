from pathlib import Path

import pytest

from trader.kernel.platform import paths


def test_trader_home_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADER_HOME", str(tmp_path))
    assert paths.project_root() == tmp_path
    assert paths.config_dir() == tmp_path / "config"
    assert paths.secrets_path() == tmp_path / "secrets" / ".env"
    assert paths.docs_dir() == tmp_path / "docs"


def test_search_up_for_pyproject(tmp_path, monkeypatch):
    monkeypatch.delenv("TRADER_HOME", raising=False)
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert paths.project_root() == tmp_path.resolve()


def test_state_dir_is_created(project: Path):
    (project / "state").rmdir()
    assert paths.state_dir().is_dir()


def test_trader_home_missing_dir_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("TRADER_HOME", str(tmp_path / "오타"))
    with pytest.raises(paths.ProjectRootNotFound, match="TRADER_HOME"):
        paths.project_root()
    assert not (tmp_path / "오타").exists()


def test_root_not_found(tmp_path, monkeypatch):
    monkeypatch.delenv("TRADER_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(paths.ProjectRootNotFound):
        paths.project_root()


def test_relative_trader_home_is_resolved(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    monkeypatch.setenv("TRADER_HOME", ".")
    root = paths.project_root()
    assert root.is_absolute() and root == tmp_path.resolve()


def test_missing_root_raises_korean_message(tmp_path, monkeypatch):
    monkeypatch.delenv("TRADER_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(paths.ProjectRootNotFound, match="pyproject.toml") as exc:
        paths.project_root()
    assert "TRADER_HOME" in str(exc.value)


def test_frozen_without_trader_home_uses_user_data_dir(tmp_path, monkeypatch):
    """번들은 cwd·실행파일 위치와 무관하게 OS 사용자 데이터 폴더를 데이터 루트로 쓴다."""
    import sys

    from trader.kernel.platform import bundle

    for name in ("HOME", "USERPROFILE"):
        monkeypatch.setenv(name, str(tmp_path / "userhome"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "userhome" / "AppData" / "Local"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "userhome" / "xdg"))
    monkeypatch.delenv("TRADER_HOME", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.chdir(tmp_path)
    root = paths.project_root()
    assert root == bundle.user_data_dir().resolve()
    assert root.is_relative_to((tmp_path / "userhome").resolve())
    assert paths.state_dir() == root / "state"
