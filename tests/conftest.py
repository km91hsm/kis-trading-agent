import os
from pathlib import Path

import pytest

from trader.kernel.config.secrets import CRED_SUFFIXES, Secrets


def secret_env_names() -> set[str]:
    """`load_secrets` 가 .env 위에 덮어쓸 수 있는 환경변수 이름 전부(모델 필드 + 계좌 전용 키)."""
    names = set(Secrets.model_fields)
    names |= {k for k in os.environ if k.startswith("KIS_") and k.endswith(CRED_SUFFIXES)}
    return names


@pytest.fixture(autouse=True)
def _isolate_secret_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """개발자 셸의 시크릿 환경변수가 테스트에 새어 실제 텔레그램·KIS 를 부르지 않게 지운다."""
    for name in secret_env_names():
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _isolate_global_lock(tmp_path_factory: pytest.TempPathFactory, monkeypatch) -> Path:
    """데몬 전역 잠금(`~/.trader/daemon-global.lock`)이 실제 홈을 건드리지 않게 테스트마다 임시
    폴더로 돌린다."""
    d = tmp_path_factory.mktemp("global-lock")
    monkeypatch.setenv("TRADER_GLOBAL_LOCK_DIR", str(d))
    return d


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """임시 프로젝트 루트를 만들고 TRADER_HOME 과 cwd 를 그곳으로 옮긴다."""
    for name in ("config", "state", "docs", "secrets"):
        (tmp_path / name).mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    monkeypatch.setenv("TRADER_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


# --- 실제 Chromium 테스트(tests/saveticker/*_browser.py)의 디스플레이 전원 조건 ---
# macOS 에서 디스플레이가 꺼져 있으면 창을 최소화해도 페이지의 document.visibilityState 가
# 'hidden' 으로 바뀌지 않아(창 서버가 가림 상태를 전달하지 않음) 숨김 수집 단언이 거짓 실패한다.
# 2026-11-02 무인 실행에서 확인. CI 는 Chromium 이 없어 이 테스트들을 이미 skip 한다.


def display_power_state(ioreg_output: str) -> int | None:
    """`ioreg -n IODisplayWrangler -r -d 1` 출력의 CurrentPowerState. 못 읽으면 None."""
    import re

    m = re.search(r'"CurrentPowerState"=(\d+)', ioreg_output)
    return int(m.group(1)) if m else None


def display_is_off(state: int | None) -> bool:
    """IODisplayWrangler 전원 상태 0~2 는 사용 불가(꺼짐·절전), 3·4 는 켜짐."""
    return state is not None and state < 3


def display_off() -> bool:
    """macOS 디스플레이가 꺼져 있으면 True. 다른 OS·조회 실패는 False(skip 하지 않음)."""
    import subprocess
    import sys

    if sys.platform != "darwin":
        return False
    try:
        out = subprocess.run(
            ["ioreg", "-n", "IODisplayWrangler", "-r", "-d", "1"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return display_is_off(display_power_state(out))


@pytest.fixture(autouse=True)
def _skip_browser_tests_when_display_off(request: pytest.FixtureRequest):
    if request.path.name.endswith("_browser.py") and display_off():
        pytest.skip("디스플레이 꺼짐 · 최소화 창의 visibility 가 전달되지 않아 Chromium 검증 불가")
