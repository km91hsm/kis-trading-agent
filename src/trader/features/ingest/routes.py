"""ingest 웹 자원: 시장 뉴스 저장소·공시 조회(토큰 없음)와 '읽은 기사 붙여 넣기'(토큰).

- `GET /news?market=&hours=48&limit=100[&ticker=]` → `news_store.recent` 행 그대로
  (`id, market, source, query, title, url, published_at, received_at, summary, lang, tickers[]`).
  촉매·테마 라벨은 scan 소유라 scan 의 `GET /news/labels?ids=` 가 따로 낸다(프런트가 합친다).
- `GET /news/events?market=&hours=72&limit=100` → 공시(`query='disclosure'`, severity 포함).
- `GET /news/manual?market=`·`POST /news/manual {market, text, url?}` → 시장 공통 수동 입력
  (`manual_inputs.read_market`/`append_market`, `state/manual_inputs/_market_<시장>.md`).
"""

from __future__ import annotations

import json
from contextlib import closing
from datetime import UTC, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from trader.features.ingest import macro_view, manual_inputs, news_store
from trader.kernel.registry.types import SseKey
from trader.kernel.web.auth import require_same_origin, require_token

router = APIRouter(prefix="/news", tags=["news"])
macro_router = APIRouter(prefix="/macro", tags=["macro"])
ROUTERS = (router, macro_router)
WRITE = [Depends(require_token), Depends(require_same_origin)]


def _market(value: str | None) -> str:
    if value not in ("KR", "US"):
        raise HTTPException(status_code=400, detail=f"시장은 KR 또는 US: {value!r}")
    return value


@macro_router.get("")
def macro_overview(request: Request, market: str = Query("KR")) -> dict:
    """최근 저장된 입력의 매크로 관측값. GET은 외부 수집이나 키 읽기를 하지 않는다."""
    m = _market(market)
    deps = request.app.state.deps
    return macro_view.overview(deps.state, m, now=deps.now())


@router.get("")
def list_news(
    request: Request,
    market: str = Query("KR"),
    hours: int = Query(48, ge=1, le=24 * 30),
    limit: int = Query(100, ge=1, le=500),
    ticker: str | None = None,
) -> dict:
    m = _market(market)
    deps = request.app.state.deps
    now = deps.now()
    with closing(deps.conn_factory()) as conn:
        items = news_store.recent(conn, m, asof=now, hours=hours, limit=limit, ticker=ticker)
        total = news_store.count(conn, m)
    return {"market": m, "hours": hours, "total": total, "asof": now.isoformat(), "items": items}


@router.get("/events")
def news_events(
    request: Request,
    market: str,
    hours: int = Query(72, ge=1, le=24 * 30),
    limit: int = Query(100, ge=1, le=500),
) -> dict:
    """최근 공시(DART·EDGAR, 중요도 severity 포함). asof 뒤에 발행·수신된 항목은 보이지 않는다.
    거시 지표는 원장에 남지 않아(세션 입력 체크포인트에만 있다) 여기서 내지 않는다."""
    m = _market(market)
    deps = request.app.state.deps
    now = deps.now().astimezone(UTC)
    since = (now - timedelta(hours=hours)).isoformat()
    with closing(deps.conn_factory()) as conn:
        rows = conn.execute(
            "SELECT id, source, title, url, published_at, received_at, severity, tickers_json "
            "FROM news_items WHERE market=? AND query='disclosure' AND received_at <= ? "
            "AND COALESCE(published_at, received_at) <= ? "
            "AND COALESCE(published_at, received_at) >= ? "
            "ORDER BY COALESCE(published_at, received_at) DESC, id DESC LIMIT ?",
            (m, now.isoformat(), now.isoformat(), since, limit),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["tickers"] = json.loads(d.pop("tickers_json") or "[]")
        out.append(d)
    return {"market": m, "hours": hours, "asof": now.isoformat(), "disclosures": out}


class ManualBody(BaseModel):
    """'읽은 기사 붙여 넣기': 첫 줄이 제목, 나머지가 메모."""

    model_config = ConfigDict(extra="forbid")

    market: Literal["KR", "US"]
    text: str = Field(max_length=4000)
    url: str = ""


@router.get("/manual")
def manual_list(request: Request, market: str) -> dict:
    """시장 공통 수동 입력 목록(최신 순). 종목별 파일은 포함하지 않는다."""
    m = _market(market)
    deps = request.app.state.deps
    return {"market": m, "items": manual_inputs.read_market(m, state=deps.state)}


@router.post("/manual", dependencies=WRITE)
def manual_add(request: Request, body: ManualBody) -> dict:
    deps = request.app.state.deps
    title, _, note = body.text.strip().partition("\n")
    try:
        return manual_inputs.append_market(
            body.market, title=title, url=body.url, note=note, now=deps.now(), state=deps.state
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _news_fp(deps: Any) -> str:
    """뉴스 저장소 건수·최대 id."""
    with closing(deps.conn_factory()) as conn:
        row = conn.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM news_items").fetchone()
    return f"{row[0]}:{row[1]}"


SSE_KEYS = (SseKey("news", _news_fp),)
