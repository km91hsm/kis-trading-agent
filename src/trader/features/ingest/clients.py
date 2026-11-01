"""수집용 클라이언트 조립(DART·EDGAR·InputDeps).

시세 클라이언트는 계좌 소유라 이 모듈이 만들지 않고 호출자가 인자로 넘긴다(ingest 는 accounts
를 import 하지 않는다). 없으면 None 으로 두어 시세 절을 건너뛴다."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from trader.features.ingest.dart import DartClient
from trader.features.ingest.edgar import EdgarClient
from trader.features.ingest.inputs import InputDeps
from trader.features.ingest.settings import SourcesConfig
from trader.kernel.config.secrets import Secrets
from trader.kernel.ledger import db
from trader.kernel.platform import paths

logger = logging.getLogger("trader.sources")

EDGAR_PLACEHOLDER = "@example.com"  # templates/config/sources.yaml 의 자리표시자
_edgar_warned = False


def _cache_dir(cache: Path | None) -> Path:
    return cache if cache is not None else paths.state_dir() / "cache"


def build_dart(secrets: Secrets, cache: Path | None = None) -> DartClient | None:
    """DART 클라이언트. API 키가 없으면 None."""
    if not secrets.DART_API_KEY:
        return None
    return DartClient(secrets.DART_API_KEY, _cache_dir(cache))


def build_edgar(
    cfg: SourcesConfig, secrets: Secrets, cache: Path | None = None
) -> EdgarClient | None:
    """EDGAR 클라이언트. `edgar.enabled` 가 꺼져 있으면 None.

    User-Agent 는 Secrets `EDGAR_USER_AGENT` 가 yaml 보다 우선이다(공개 저장소에 개인 이메일을
    두지 않는다). 비었거나 자리표시자만 있으면 SEC 에 보내지 않고 None(경고는 프로세스당 1회)."""
    global _edgar_warned
    if not cfg.edgar.enabled:
        return None
    ua = (secrets.EDGAR_USER_AGENT or cfg.edgar.user_agent or "").strip()
    if not ua or EDGAR_PLACEHOLDER in ua:
        if not _edgar_warned:
            _edgar_warned = True
            logger.warning(
                "EDGAR User-Agent 미설정 — EDGAR 수집을 끕니다 "
                "(설정 화면 '연결 키' 또는 secrets/.env 의 EDGAR_USER_AGENT)"
            )
        return None
    return EdgarClient(ua, _cache_dir(cache))


def build_input_deps(
    secrets: Secrets,
    cfg: SourcesConfig,
    *,
    market: Any | None = None,
    conn_factory: Callable[[], Any] | None = None,
) -> InputDeps:
    """수집용 의존성 묶음. `market` 은 호출자가 만든 시세 클라이언트(없으면 시세 절 건너뜀)."""
    return InputDeps(
        market=market,
        dart=build_dart(secrets),
        edgar=build_edgar(cfg, secrets),
        conn_factory=conn_factory if conn_factory is not None else db.connect,
    )
