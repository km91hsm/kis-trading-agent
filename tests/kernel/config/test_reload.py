import os

import pytest
from pydantic import BaseModel, ConfigDict

from trader.kernel.config.reload import SettingsWatcher
from trader.kernel.config.sections import Section, SectionRegistry


class A(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int = 0


@pytest.fixture
def reg() -> SectionRegistry:
    r = SectionRegistry()
    r.register(Section("settings", "a", A, A))
    return r


def write(p, text: str) -> None:
    """같은 초 안의 연속 쓰기도 변경으로 보이게 mtime 을 밀어 둔다."""
    old = p.stat().st_mtime_ns if p.exists() else 0
    p.write_text(text, encoding="utf-8")
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, max(st.st_mtime_ns, old) + 1_000_000))


def test_poll_returns_none_until_file_changes(project, reg):
    p = project / "config/settings.yaml"
    write(p, "a: {x: 1}\n")
    w = SettingsWatcher(reg, path=p)
    assert w.poll() is None
    write(p, "a: {x: 2}\n")
    new = w.poll()
    assert new is not None and new.a.x == 2
    assert w.poll() is None  # 같은 내용은 한 번만 돌려준다


def test_invalid_change_keeps_old_and_reports_once(project, reg):
    p = project / "config/settings.yaml"
    write(p, "a: {x: 1}\n")
    errors: list[str] = []
    w = SettingsWatcher(reg, path=p, on_error=errors.append)
    write(p, "a: {x: nope}\n")
    assert w.poll() is None
    assert len(errors) == 1 and "settings.yaml" in errors[0]
    # 같은 오류로 다시 저장해도 다시 알리지 않는다
    write(p, "a: {x: nope}\n")
    assert w.poll() is None
    assert len(errors) == 1
    # 고치면 반영되고, 이후 같은 오류가 다시 나면 다시 알린다
    write(p, "a: {x: 5}\n")
    assert w.poll().a.x == 5
    write(p, "a: {x: nope}\n")
    assert w.poll() is None
    assert len(errors) == 2


def test_on_error_failure_is_swallowed(project, reg):
    p = project / "config/settings.yaml"
    write(p, "a: {x: 1}\n")

    def boom(msg: str) -> None:
        raise RuntimeError("x")

    w = SettingsWatcher(reg, path=p, on_error=boom)
    write(p, "zzz: 1\n")
    assert w.poll() is None
