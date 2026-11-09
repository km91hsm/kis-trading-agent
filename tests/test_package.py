def test_version():
    import trader

    assert trader.__version__ == "0.3.10"


def test_project_fixture_sets_home(project, monkeypatch):
    import os

    assert os.environ["TRADER_HOME"] == str(project)
    assert (project / "pyproject.toml").exists()
