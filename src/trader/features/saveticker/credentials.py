"""세이브티커 이메일 로그인 자격 증명: `secrets/.env` 의 `SAVETICKER_EMAIL`·`SAVETICKER_PASSWORD`.

KIS 키와 같은 파일·같은 편집 헬퍼(잠금·임시 파일 바꿔 넣기·0600)를 쓴다. 값은 Native host 가
확장의 자동 로그인 요청에만 돌려주며 로그·진단·하트비트에 남기지 않는다."""

from __future__ import annotations

import os

from dotenv import dotenv_values

from trader.kernel.config.errors import ConfigError
from trader.kernel.config.secrets import editing_env, write_env
from trader.kernel.platform import paths

EMAIL, PASSWORD = "SAVETICKER_EMAIL", "SAVETICKER_PASSWORD"


def _quote(value: str) -> str:
    # python-dotenv 의 큰따옴표 값은 \\ 와 \" 이스케이프를 푼다 — '#', 공백, '=' 도 안전.
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def load() -> tuple[str, str] | None:
    """(이메일, 비밀번호). 하나라도 없으면 None. 비밀번호는 `${…}` 치환·앞뒤 공백 제거 없이 그대로
    읽는다(공용 `load_secrets` 는 둘 다 하므로 쓰지 않는다). 비어 있지 않은 환경변수가 우선."""
    p = paths.secrets_path()
    values: dict[str, str | None] = {}
    if p.exists():
        try:
            values = dotenv_values(p, encoding="utf-8", interpolate=False)
        except UnicodeDecodeError as e:
            raise ConfigError(f"{p.name} 은 UTF-8 로 저장해야 합니다: {e}") from e
    email = os.environ.get(EMAIL) or values.get(EMAIL) or ""
    password = os.environ.get(PASSWORD) or values.get(PASSWORD) or ""
    email = email.strip()
    return (email, password) if email and password else None


def stored() -> bool:
    return load() is not None


def save(email: str, password: str) -> None:
    # 줄바꿈류·제어문자는 `.env` 줄을 쪼갤 수 있다(`write_env` 는 splitlines 로 다시 읽는다).
    if (
        "@" not in email
        or not password
        or any(not c.isprintable() and c != " " for c in email + password)
    ):
        raise ValueError("이메일과 비밀번호를 확인하세요")
    p = paths.secrets_path()
    with editing_env(p):
        write_env(p, {EMAIL: _quote(email.strip()), PASSWORD: _quote(password)})


def clear() -> None:
    p = paths.secrets_path()
    with editing_env(p):
        if p.exists():
            write_env(p, {}, remove={EMAIL, PASSWORD})
