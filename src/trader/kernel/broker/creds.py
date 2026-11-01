"""KIS 앱키 묶음과 지문. 키 자체는 파일명·로그에 남기지 않는다."""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict

from trader.kernel.config import Env


def key_fingerprint(app_key: str) -> str:
    """앱키 지문(sha256 앞 12자) — 파일명·캐시 확인용. 키 자체는 남기지 않는다."""
    return hashlib.sha256(app_key.encode("utf-8")).hexdigest()[:12]


class KisCreds(BaseModel):
    """한 앱키 묶음. prefix 는 실제로 고른 접두사(공용이면 `KIS_<ENV>_`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    env: Env
    prefix: str
    app_key: str | None = None
    app_secret: str | None = None
    cano: str | None = None
    hts_id: str | None = None

    @property
    def shared(self) -> bool:
        return self.prefix == f"KIS_{self.env.upper()}_"

    @property
    def bucket(self) -> str:
        """토큰 캐시·레이트리미터 이름(파일명 안전): `<env>-<앱키 지문>` — 접두사가 아니라 앱키로
        나눠야 같은 키를 두 이름으로 넣어도 한도·토큰을 하나로 쓰고, 키를 바꾸면 새 캐시가 된다."""
        if not self.app_key:
            return self.env
        return f"{self.env}-{key_fingerprint(self.app_key)}"
