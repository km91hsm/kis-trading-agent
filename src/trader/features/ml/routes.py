"""ml 웹 자원(조회 전용, 토큰 없음): 피처 스토어 행 탐색과 모델 레지스트리.

- `GET /features/latest?market=` → 종목별 최신 피처 행 + `config/sectors.yaml` 섹터.
- `GET /features/history?market=&ticker=&days=120` → 한 종목 최근 days 행(날짜 오름차순). 유니버스에
  없는 종목은 파일을 다 읽지 않고 404(토큰 없는 API 비용 방어).
- `GET /ml/models` → 레지스트리 요약 목록 + 승격 모델, `GET /ml/models/{version}` → 메타 전체.
- SSE 키 `features`: 피처 스토어 최신 날짜·mtime + 모델 레지스트리 최신 버전·승격 버전 지문.
ml 의 공개면(`ml.api`)만 쓴다. 섹터·유니버스는 `ingest.api`.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Request

from trader.features.ingest import api as ingest
from trader.features.ml import api as ml
from trader.kernel.config import ConfigError
from trader.kernel.registry.types import SseKey

logger = logging.getLogger("trader.ml")

features_router = APIRouter(prefix="/features", tags=["features"])
ml_router = APIRouter(prefix="/ml", tags=["ml"])
ROUTERS = (features_router, ml_router)

_TICKER_RE = re.compile(r"^[A-Za-z0-9.\-]{1,12}$")
_VERSION_RE = re.compile(r"^\d{8}_\d{6}$")
COLUMNS = [*ml.FEATURE_COLUMNS, "sector"]
SUMMARY_KEYS = (
    "name", "version", "saved_at", "promoted", "promote_reason", "promoted_at", "market", "rows",
    "train_range", "backend", "metrics",
)  # fmt: skip
NOTE = "승격(promoted)된 최신 모델만 05_quant·scan 랭킹에 쓰인다"


def _market(value: str) -> str:
    if value not in ("KR", "US"):
        raise HTTPException(status_code=400, detail=f"시장은 KR 또는 US: {value!r}")
    return value


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """NaN/NA → None, 날짜 → ISO (JSON 에 NaN 을 내보내지 않는다)."""
    out: list[dict[str, Any]] = []
    for row in df.to_dict(orient="records"):
        rec: dict[str, Any] = {}
        for k, v in row.items():
            if v is None or v is pd.NA or (isinstance(v, float) and pd.isna(v)):
                rec[k] = None
            elif isinstance(v, pd.Timestamp):
                rec[k] = v.date().isoformat()
            elif isinstance(v, date):
                rec[k] = v.isoformat()
            elif hasattr(v, "item"):  # numpy 스칼라
                rec[k] = None if pd.isna(v) else v.item()
            else:
                rec[k] = v
        out.append(rec)
    return out


def _sectors() -> ingest.SectorMap:
    try:
        return ingest.load_sectors()
    except ConfigError as e:  # 설정 파일 오류는 조회를 막지 않고 섹터만 비운다
        logger.warning("sectors.yaml 로드 실패: %s", e)
        return ingest.SectorMap()


def _universe_tickers(market: str) -> list[str] | None:
    try:
        u = ingest.load_universe()
    except ConfigError:
        return None
    members = u.kr if market == "KR" else [s.symbol for s in u.us]
    return members or None


@features_router.get("/latest")
def latest(request: Request, market: str) -> dict:
    deps = request.app.state.deps
    m = _market(market)
    rows = _records(ml.latest_rows(deps.state, m, tickers=_universe_tickers(m)))
    sectors = _sectors()
    for r in rows:
        r["sector"] = sectors.sector_of(str(r["ticker"]), m)  # type: ignore[arg-type]
    asof = max((r["date"] for r in rows), default=None)
    return {"market": m, "asof": asof, "columns": COLUMNS, "rows": rows}


@features_router.get("/history")
def history(
    request: Request, market: str, ticker: str, days: int = Query(120, ge=5, le=1000)
) -> dict:
    deps = request.app.state.deps
    m = _market(market)
    if not _TICKER_RE.fullmatch(ticker):
        raise HTTPException(status_code=400, detail=f"ticker 형식 오류: {ticker!r}")
    known = _universe_tickers(m)
    if known is not None and ticker not in known:
        raise HTTPException(status_code=404, detail=f"유니버스에 없는 종목: {ticker}")
    df = ml.ticker_history(deps.state, m, ticker, days=days)
    return {"ticker": ticker, "market": m, "rows": _records(df)}


def _summary(meta: dict) -> dict:
    out = {k: meta.get(k) for k in SUMMARY_KEYS}
    metrics = meta.get("metrics") or {}
    out["metrics"] = {k: metrics.get(k) for k in ("accuracy", "baseline", "alpha", "n_test")}
    return out


@ml_router.get("/models")
def models(request: Request) -> dict:
    metas = ml.list_models("quant", request.app.state.deps.state)
    active = next((m for m in metas if m.get("promoted")), None)
    return {
        "models": [_summary(m) for m in metas],
        "active": _summary(active) if active else None,
        "note": NOTE,
    }


@ml_router.get("/models/{version}")
def model_detail(request: Request, version: str) -> dict:
    if not _VERSION_RE.fullmatch(version):
        raise HTTPException(status_code=400, detail="버전 형식 오류")
    for m in ml.list_models("quant", request.app.state.deps.state):
        if m.get("version") == version:
            return m
    raise HTTPException(status_code=404, detail=f"모델 없음: {version}")


def _features_fp(deps: Any) -> str:
    """피처 스토어 최신 asof·디렉터리 mtime(같은 날 재스냅샷) + 레지스트리 최신·승격 버전."""
    metas = ml.list_models("quant", deps.state)
    newest = metas[0].get("version") if metas else None
    active = next((m.get("version") for m in metas if m.get("promoted")), None)
    return f"{ml.latest_day(deps.state)}:{ml.store_mtime(deps.state)}:{newest}:{active}"


SSE_KEYS = (SseKey("features", _features_fp),)
