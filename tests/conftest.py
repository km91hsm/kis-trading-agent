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


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """임시 프로젝트 루트를 만들고 TRADER_HOME 과 cwd 를 그곳으로 옮긴다."""
    for name in ("config", "state", "docs", "secrets"):
        (tmp_path / name).mkdir()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    monkeypatch.setenv("TRADER_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path
