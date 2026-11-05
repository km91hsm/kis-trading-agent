"""`secrets/.env` 로더: `Secrets` 모델과 계좌별 KIS 자격증명 선택, `.env` 안전 편집(잠금·임시
파일 바꿔 넣기·0600)과 웹 토큰 자동 생성(`ensure_web_token`).

시크릿 값은 로그·예외 메시지에 넣지 않는다(이름만)."""

from __future__ import annotations

import contextlib
import os
import re
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, ValidationError

from trader.kernel.config.errors import ConfigError
from trader.kernel.config.loader import _describe
from trader.kernel.platform import paths
from trader.kernel.platform.filelock import locked
from trader.kernel.platform.fsutil import atomic_replace

if TYPE_CHECKING:
    from trader.kernel.broker.creds import KisCreds
    from trader.kernel.config import Env

CRED_PREFIX_RE = re.compile(r"^KIS_[A-Z0-9]+(?:_[A-Z0-9]+)*_$")
CRED_SUFFIXES = ("APP_KEY", "APP_SECRET", "CANO", "HTS_ID")


def account_prefix(account_id: str, credentials: str | None = None) -> str:
    """계좌 전용 키 접두사: `credentials` 가 우선, 없으면 id → `KIS_<ID>_`(영숫자 외는 _)."""
    if credentials:
        return credentials
    return "KIS_" + re.sub(r"[^A-Z0-9]+", "_", account_id.upper()).strip("_") + "_"


class Secrets(BaseModel):
    """필드명 = 환경변수명. 비어 있지 않은 환경변수가 .env 값을 덮어쓴다. 계좌 전용 키
    (`KIS_<접두사>_APP_KEY` 등, `CRED_SUFFIXES`)는 필드가 아니라 추가 값으로 들고 있다."""

    model_config = ConfigDict(extra="allow")

    KIS_PAPER_APP_KEY: str | None = None
    KIS_PAPER_APP_SECRET: str | None = None
    KIS_REAL_APP_KEY: str | None = None
    KIS_REAL_APP_SECRET: str | None = None
    # 계좌번호(8자리). accounts.yaml 의 cano 가 자리표시자("00000000"·빈 값)면 env 별로
    # 여기서 읽는다 (저장소를 공개해도 커밋에 계좌번호가 남지 않도록)
    KIS_PAPER_CANO: str | None = None
    KIS_REAL_CANO: str | None = None
    DART_API_KEY: str | None = None
    TELEGRAM_BOT_TOKEN: str | None = None
    TELEGRAM_CHAT_ID: str | None = None
    # 그룹 채팅에서 승인·재개·모드 등 쓰기 명령을 보낼 수 있는 사용자 id(쉼표).
    # 비우면 1:1 채팅에서만 쓰기 명령
    TELEGRAM_ALLOWED_USERS: str | None = None
    NAVER_CLIENT_ID: str | None = None
    NAVER_CLIENT_SECRET: str | None = None
    FRED_API_KEY: str | None = None
    ECOS_API_KEY: str | None = None
    # SEC EDGAR User-Agent(이름 + 연락 이메일 — 공개 저장소의 sources.yaml 에 두지 않는다)
    EDGAR_USER_AGENT: str | None = None
    WEB_TOKEN: str | None = None
    REDDIT_CLIENT_ID: str | None = None
    REDDIT_CLIENT_SECRET: str | None = None
    REDDIT_USER: str | None = None
    # 체결통보 웹소켓 구독 키(HTS ID). 없으면 정기 체결 동기화만
    KIS_HTS_ID: str | None = None

    def get(self, name: str) -> str | None:
        """필드 또는 계좌 전용 추가 값(없거나 빈 값이면 None)."""
        v = getattr(self, name, None) if name in Secrets.model_fields else None
        if v is None:
            v = (self.model_extra or {}).get(name)
        v = (str(v).strip() if v is not None else "") or None
        return v

    def kis_creds(self, account: Any | None = None, *, env: Env | None = None) -> KisCreds:
        """계좌(없으면 env 공용)의 KIS 자격증명. 계좌 전용 접두사에 APP_KEY 가 있으면 그 묶음,
        아니면 env 공용(`KIS_PAPER_*`·`KIS_REAL_*`). HTS ID 는 접두사 값 → `KIS_HTS_ID`.
        account 는 `.id`·`.env` 와 선택적 `.credentials` 를 가진 객체."""
        from trader.kernel.broker.creds import KisCreds  # 순환 import 회피

        e: Env = account.env if account is not None else (env or "real")
        default = f"KIS_{e.upper()}_"
        prefix = (
            account_prefix(account.id, getattr(account, "credentials", None))
            if account is not None
            else default
        )
        if prefix != default and not self.get(prefix + "APP_KEY"):
            prefix = default
        return KisCreds(
            env=e,
            prefix=prefix,
            app_key=self.get(prefix + "APP_KEY"),
            app_secret=self.get(prefix + "APP_SECRET"),
            cano=self.get(prefix + "CANO"),
            hts_id=self.get(prefix + "HTS_ID") or self.get("KIS_HTS_ID"),
        )


def load_secrets(path: Path | None = None) -> Secrets:
    """`.env`(UTF-8) 위에 비어 있지 않은 환경변수를 덮어 `Secrets` 를 만든다."""
    p = path or paths.secrets_path()
    values: dict[str, str | None] = {}
    if p.exists():
        try:
            values.update(dotenv_values(p, encoding="utf-8"))
        except UnicodeDecodeError as e:
            raise ConfigError(f"{p.name} 은 UTF-8 로 저장해야 합니다 (cp949 등 감지): {e}") from e
    for key in Secrets.model_fields:
        env = (os.environ.get(key) or "").strip()
        if env:
            values[key] = env
    for key, raw in os.environ.items():  # 계좌 전용 키도 환경변수가 덮어쓴다
        if key.startswith("KIS_") and key.endswith(CRED_SUFFIXES) and raw.strip():
            values[key] = raw.strip()
    cleaned = {k: v.strip() for k, v in values.items() if v and v.strip()}
    try:
        return Secrets.model_validate(cleaned)
    except ValidationError as e:
        raise ConfigError(f"{p.name} 검증 실패: {_describe(e)}") from e


# --- .env 편집 (accounts `secrets_io` 도 이것을 쓴다) ---------------------------


def read_env_file(path: Path) -> dict[str, str]:
    """`.env` 의 키 → 값(앞뒤 공백 제거). 파일이 없으면 빈 dict."""
    if not path.exists():
        return {}
    try:
        raw = dotenv_values(path, encoding="utf-8")
    except UnicodeDecodeError as e:
        raise ConfigError(f"{path.name} 은 UTF-8 로 저장해야 합니다: {e}") from e
    return {k: (v or "").strip() for k, v in raw.items()}


_THREAD_LOCK = threading.Lock()


@contextlib.contextmanager
def editing_env(dst: Path) -> Iterator[None]:
    """백업·읽기·고치기·바꿔 넣기 전체를 한 번에 하나만(같은 프로세스의 웹 요청 스레드 둘, 데몬과
    CLI 처럼 다른 프로세스 둘이 동시에 저장해도 한쪽 변경이 사라지지 않게)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    with _THREAD_LOCK, locked(dst.with_name(dst.name + ".lock"), timeout=10.0):
        yield


def write_env(
    dst: Path, values: dict[str, str], remove: set[str] | frozenset = frozenset()
) -> None:
    """있는 줄은 자리에서 바꾸고 없는 키는 끝에 붙인다(주석·순서 보존), remove 의 키 줄은 뺀다.
    임시 파일에 쓴 뒤 바꿔 넣고(쓰다 끊겨도 원본이 남는다) 파일 권한은 0600."""
    lines = dst.read_text(encoding="utf-8").splitlines() if dst.exists() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        is_kv = "=" in line and not line.lstrip().startswith("#")
        key = line.split("=", 1)[0].strip() if is_kv else None
        if key is not None and key.startswith("export "):
            key = key[len("export ") :].strip()
        if key in remove:
            continue
        if key in values:
            out.append(f"{key}={values[key]}")
            seen.add(key)
        else:
            out.append(line)
    for k, v in values.items():
        if k not in seen:
            out.append(f"{k}={v}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f"{dst.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    tmp.write_text("\n".join(out).rstrip("\n") + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    atomic_replace(tmp, dst)


def ensure_web_token(path: Path | None = None) -> tuple[str, bool]:
    """WEB_TOKEN 이 없으면 만들어 .env 에 넣는다(첫 실행에 제어 API 가 통째로 닫히지 않게 —
    `trader app` 이 첫 실행에 부른다). (토큰, 새로 만들었는가). 비어 있지 않은 환경변수
    `WEB_TOKEN` 이 우선이다. 값은 반환만 하고 로그에 남기지 않는다."""
    import secrets as _secrets

    env = (os.environ.get("WEB_TOKEN") or "").strip()
    if env:
        return env, False
    p = path or paths.secrets_path()
    with editing_env(p):
        current = read_env_file(p).get("WEB_TOKEN")
        if current:
            return current, False
        token = _secrets.token_urlsafe(24)
        write_env(p, {"WEB_TOKEN": token})
    return token, True
