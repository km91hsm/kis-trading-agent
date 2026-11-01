"""universe.yaml · sectors.yaml 로더."""

import os
import shutil

import pydantic
import pytest

from tests.features.ingest.helpers import write_yaml
from trader.features.ingest.universe import load_sectors, load_universe
from trader.kernel.config import ConfigError
from trader.kernel.platform.bundle import templates_dir


def test_universe_load(project):
    write_yaml(
        project,
        "universe.yaml",
        {
            "kr": ["005930"],
            "us": [{"symbol": "AAPL", "exchange": "NAS"}],
            "exclude": [],
            "watch": [],
        },
    )
    u = load_universe()
    assert u.kr == ["005930"] and u.us[0].exchange == "NAS"


def test_universe_and_sectors_memo_by_file_mtime(project):
    """같은 파일이면 같은(frozen) 객체, 파일이 바뀌면 다시 읽는다."""
    write_yaml(project, "universe.yaml", {"kr": ["005930"], "us": []})
    a = load_universe()
    assert load_universe() is a
    with pytest.raises(pydantic.ValidationError):
        a.kr = ["000660"]  # frozen — 나눠 쓰는 객체를 바꾸지 못한다
    path = project / "config" / "universe.yaml"
    write_yaml(project, "universe.yaml", {"kr": ["000660"], "us": []})
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    assert load_universe().kr == ["000660"]
    s1 = load_sectors()
    assert load_sectors() is s1


def test_universe_bad_exchange(project):
    write_yaml(project, "universe.yaml", {"kr": [], "us": [{"symbol": "X", "exchange": "LSE"}]})
    with pytest.raises(ConfigError):
        load_universe()


def test_universe_template_loads(project):
    for name in ("universe.yaml", "sectors.yaml"):
        shutil.copy(templates_dir() / "config" / name, project / "config" / name)
    u = load_universe()
    assert len(u.kr) > 100 and u.us
    s = load_sectors()
    assert s.sectors and set(s.kr.values()) <= set(s.sectors)
