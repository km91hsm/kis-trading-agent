"""ml 포트: 레인·ingest 가 `Boot.ports["ml"]` 로 받는 정량 모델 조회·보고·피처 스냅샷
(커널 `execution.ports.MlPort`)."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pandas as pd

from trader.features.ingest import api as ingest_api
from trader.features.ml import features, quant_report
from trader.features.ml import registry as model_registry
from trader.features.ml.settings import load_ml
from trader.kernel.platform import paths
from trader.kernel.registry.types import Port

logger = logging.getLogger("trader.ml")


class MlPortImpl:
    def __init__(
        self, settings: Any, registry: Any = None, ports: Any = None, secrets: Any = None
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._ports = ports if ports is not None else {}  # 생성 뒤에 채워지므로 지연 해석
        self._secrets = secrets

    def quant_enabled(self) -> bool:
        try:
            return bool(load_ml(self._registry).models.quant.enabled)
        except Exception as e:  # noqa: BLE001 - 설정 오류는 퀀트 끔으로 본다
            logger.warning("ML 설정 읽기 실패 — 퀀트 끔: %s", e)
            return False

    def latest_model(self, market: str | None) -> tuple[Any, dict] | None:
        return model_registry.latest("quant", state=paths.state_dir(), market=market)

    def latest_row(self, ticker: str, market: str, *, asof: date | None) -> dict | None:
        return quant_report.latest_row(ticker, market, paths.state_dir(), asof=asof)

    def build_report(self, model: Any, meta: dict, row: dict, *, ticker: str, asof: str) -> dict:
        return quant_report.build(model, meta, row, ticker=ticker, asof=asof)

    def peer_medians(
        self, *, market: str, ticker: str, asof_date: date, sectors: Any
    ) -> dict | None:
        """스펙 §3 '동종업 중앙값 대비'(옛 `_peer_section`): 피처 스토어 최신 행(그 날 이하)으로
        섹터 중앙값·내 값·차이. 섹터를 모르거나 구성원이 3개 미만이면 None."""
        sector = sectors.sector_of(ticker, market)
        if sector is None:
            return None
        universe = ingest_api.load_universe()
        members = universe.kr if market == "KR" else [s.symbol for s in universe.us]
        members = sorted({t for t in members if sectors.sector_of(t, market) == sector} | {ticker})
        stats = features.sector_medians(None, market, sector, tickers=members, asof=asof_date)
        if stats["n"] < features.MIN_SECTOR_PEERS:
            return None
        mine_rows = features.latest_rows(
            None, market, tickers=[ticker], asof=asof_date, columns=list(features.RATIO_COLUMNS)
        )
        mine: dict[str, float | None] = dict.fromkeys(features.RATIO_COLUMNS)
        if not mine_rows.empty:
            for col in features.RATIO_COLUMNS:
                v = mine_rows.iloc[0][col]
                mine[col] = None if v is None or v is pd.NA or pd.isna(v) else float(v)
        names = features.RATIO_COLUMNS  # 열 → 한글 비율 키 (basic_ratios 와 같은 키)
        median = {names[c]: stats["median"][c] for c in names}
        mine_k = {names[c]: mine[c] for c in names}
        diff = {
            k: (None if mine_k[k] is None or median[k] is None else mine_k[k] - median[k])
            for k in median
        }
        return {
            "sector": sector,
            "n": stats["n"],
            "dates": stats["dates"],
            "median": median,
            "mine": mine_k,
            "diff": diff,
        }

    def snapshot(
        self, markets: list[str], *, tickers: list[tuple[str, str | None]] | None = None
    ) -> int:
        port = self._ports.get("accounts")
        market_client = port.market_client() if port is not None else None
        if market_client is None:
            raise RuntimeError("시세 클라이언트 없음(피처 스냅샷 불가)")
        secrets = self._secrets
        return features.snapshot(
            market_client=market_client,
            dart=ingest_api.build_dart(secrets),
            edgar=ingest_api.build_edgar(ingest_api.load_sources(self._registry), secrets),
            universe=ingest_api.load_universe(),
            markets=markets,  # type: ignore[arg-type]
            tickers=tickers,
        )


def build_port(boot: Any) -> MlPortImpl:
    return MlPortImpl(
        boot.settings,
        getattr(boot, "registry", None),
        getattr(boot, "ports", None),
        getattr(boot, "secrets", None),
    )


PORT = Port("ml", build_port)
