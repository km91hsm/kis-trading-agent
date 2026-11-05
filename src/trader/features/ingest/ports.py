"""ingest 포트: 레인이 `Boot.ports["inputs"]` 로 받는 00_inputs 수집 구현체."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trader.features.ingest import news_store
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

    def __init__(
        self, secrets: Secrets, cfg: SourcesConfig, ports: Mapping[str, Any] | None = None
    ) -> None:
        self._secrets = secrets
        self._cfg = cfg
        # `Boot.ports` 자체 — 포트는 구현이 만들어진 뒤에 채워지므로 collect 때 풀어 쓴다
        self._ports = ports if ports is not None else {}

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
        ml = self._ports.get("ml")  # 덕 타이핑 — features.ml 을 import 하지 않는다
        if ml is not None:
            deps.peer_fn = ml.peer_medians
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

    def recent(
        self,
        conn: Any,
        market: str,
        *,
        tickers: Iterable[str] | None,
        since: datetime,
        limit: int = 20,
        novel_only: bool = False,
        asof: datetime | None = None,
        untagged_only: bool = False,
    ) -> list[dict]:
        """since 이후·asof(없으면 지금) 이전 수신 뉴스(`news_store.received_since`)."""
        return news_store.received_since(
            conn,
            market,
            since=since,
            asof=asof or datetime.now(UTC),
            tickers=tickers,
            novel_only=novel_only,
            untagged_only=untagged_only,
            limit=limit,
        )


def build_port(boot: Any) -> InputsPortImpl:
    return InputsPortImpl(boot.secrets, load_sources(registry=boot.registry), boot.ports)


PORT = Port("inputs", build_port)
