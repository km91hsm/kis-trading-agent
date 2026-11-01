import os

import pytest
from pydantic import BaseModel

from trader.kernel.config.errors import ConfigError
from trader.kernel.config.loader import load, memo_load
from trader.kernel.config.sections import Section, SectionRegistry
from trader.kernel.config.time import KST
from trader.kernel.config.yaml_io import read_yaml


class A(BaseModel):
    x: int = 0


class B(BaseModel):
    y: int = 0


@pytest.fixture
def reg() -> SectionRegistry:
    r = SectionRegistry()
    r.register(Section("settings", "a", A, A))
    r.register(Section("settings", "b", B, B))
    return r


def test_kst_is_seoul():
    assert str(KST) == "Asia/Seoul"


def test_load_merges_sections_and_forbids_unknown(project, reg):
    (project / "config/settings.yaml").write_text("a: {x: 1}\nb: {y: 2}\n")
    cfg = load("settings", reg)
    assert cfg.a.x == 1 and cfg.b.y == 2
    (project / "config/settings.yaml").write_text("a: {x: 1}\nzzz: 1\n")
    with pytest.raises(ConfigError, match=r"settings\.yaml.*zzz"):
        load("settings", reg)


def test_missing_file_uses_defaults_when_all_have_defaults(project, reg):
    cfg = load("settings", reg)
    assert cfg.a.x == 0 and cfg.b.y == 0


def test_missing_file_without_defaults_raises(project):
    r = SectionRegistry()
    r.register(Section("settings", "a", A))
    with pytest.raises(ConfigError, match=r"settings\.yaml"):
        load("settings", r)


def test_bad_value_raises_config_error(project, reg):
    (project / "config/settings.yaml").write_text("a: {x: nope}\n")
    with pytest.raises(ConfigError, match=r"settings\.yaml.*a\.x"):
        load("settings", reg)


def test_explicit_path_overrides_config_dir(tmp_path, reg):
    p = tmp_path / "other.yaml"
    p.write_text("a: {x: 7}\n")
    assert load("settings", reg, path=p).a.x == 7


def test_read_yaml_errors(tmp_path):
    p = tmp_path / "x.yaml"
    assert read_yaml(p) == {}
    p.write_text("")
    assert read_yaml(p) == {}
    p.write_text("- 1\n")
    with pytest.raises(ConfigError, match="매핑"):
        read_yaml(p)
    p.write_text("a: [\n")
    with pytest.raises(ConfigError, match="구문"):
        read_yaml(p)
    p.write_bytes("a: 한글\n".encode("cp949"))
    with pytest.raises(ConfigError, match="UTF-8"):
        read_yaml(p)


def test_memo_load_reuses_until_file_changes(project, reg):
    p = project / "config/settings.yaml"
    p.write_text("a: {x: 1}\n")
    first = memo_load("settings", reg)
    assert memo_load("settings", reg) is first
    p.write_text("a: {x: 22}\n")
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    again = memo_load("settings", reg)
    assert again is not first and again.a.x == 22


def test_memo_load_does_not_cache_errors(project, reg):
    p = project / "config/settings.yaml"
    p.write_text("zzz: 1\n")
    with pytest.raises(ConfigError):
        memo_load("settings", reg)
    p.write_text("a: {x: 3}\n")
    assert memo_load("settings", reg).a.x == 3
