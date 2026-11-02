"""ingest 포트: 레인이 `Boot.ports["inputs"]` 로 받는 00_inputs 수집 구현체."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from trader.features.ingest.api import (
    SourcesConfig,
    build_input_deps,
    collect_and_save,
    load_sources,
)
from trader.kernel.config.secrets import Secrets
from trader.kernel.registry.types import Port


class InputsPortImpl:
    """종목 하나의 `00_inputs` 체크포인트를 만들거나(없을 때·force) 읽어 돌려준다."""

    def __init__(self, secrets: Secrets, cfg: SourcesConfig) -> None:
        self._secrets = secrets
        self._cfg = cfg

    def collect(
        self,
        *,
        ticker: str,
        market: str,
        exchange: str | None,
        asof: datetime,
        force: bool,
        conn_factory: Callable[[], Any] | None,
        market_client: Any | None = None,
        state: Path | None = None,
    ) -> dict:
        """`market` 은 시장 코드(KR/US), `market_client` 는 시세 클라이언트(없으면 시세 절 생략).
        반환은 저장된 `00_inputs.json` 의 내용(payload dict)."""
        deps = build_input_deps(
            self._secrets, self._cfg, market=market_client, conn_factory=conn_factory
        )
        path = collect_and_save(
            ticker=ticker,
            market=market,  # type: ignore[arg-type]  # "KR"|"US" 문자열
            exchange=exchange,
            asof=asof,
            cfg=self._cfg,
            secrets=self._secrets,
            deps=deps,
            state=state,
            force=force,
        )
        return json.loads(path.read_text(encoding="utf-8"))


PORT = Port("inputs", lambda b: InputsPortImpl(b.secrets, load_sources(registry=b.registry)))
