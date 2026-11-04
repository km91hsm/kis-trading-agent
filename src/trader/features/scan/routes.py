"""scan 웹 자원: 뉴스 라벨·테마 열기(조회, 토큰 없음)와 관심종목(조회 토큰 없음, 변경 토큰).

- `GET /news/labels?ids=1,2` → `{id: {catalyst, themes}}`. 촉매는 `theme.is_catalyst(source)`,
  테마는 그 시장 최신 테마 열기(control `theme_heat:<시장>`) 상위 `theme.top_n` 개 중 기사 종목이
  속한 것. 뉴스 행은 ingest 의 `news_items` 표에서 id 로 읽는다(데이터 접근, README).
- `GET /news/themes?market=&n=10` → 최신 테마 열기 `{market, date, observed_at, top_n, themes}`.
- `GET /settings/watchlist`·`PATCH /settings/watchlist {tickers}` → `watchlist.set_watchlist`
  (channel `web`, actor 토큰 지문). 형식 오류는 400.
"""

from __future__ import annotations

import json
from contextlib import closing
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from trader.features.scan import theme, watchlist
from trader.kernel.ledger.control import get_flag
from trader.kernel.registry.types import SseKey
from trader.kernel.web.auth import actor_of, require_same_origin, require_token

router = APIRouter(tags=["scan"])
ROUTERS = (router,)
WRITE = [Depends(require_token), Depends(require_same_origin)]
MAX_LABEL_IDS = 500


def _market(value: str | None) -> str:
    if value not in ("KR", "US"):
        raise HTTPException(status_code=400, detail=f"시장은 KR 또는 US: {value!r}")
    return value


def _ids(raw: str) -> list[int]:
    out = []
    for x in (s.strip() for s in raw.split(",")):
        if not x:
            continue
        if not (x.isascii() and x.isdigit()):
            raise HTTPException(status_code=400, detail=f"id 형식 오류: {x!r}")
        out.append(int(x))
    if len(out) > MAX_LABEL_IDS:
        raise HTTPException(status_code=400, detail=f"한 번에 {MAX_LABEL_IDS}건까지")
    return list(dict.fromkeys(out))


@router.get("/news/labels")
def news_labels(request: Request, ids: str = "") -> dict[str, dict]:
    want = _ids(ids)
    if not want:
        return {}
    deps = request.app.state.deps
    top_n = deps.settings.theme.top_n
    with closing(deps.conn_factory()) as conn:
        rows = conn.execute(
            f"SELECT id, market, source, tickers_json FROM news_items "
            f"WHERE id IN ({','.join('?' * len(want))})",
            want,
        ).fetchall()
        members: dict[str, dict[str, list[str]]] = {}
        for m in {r["market"] for r in rows}:
            heat = get_flag(conn, f"{theme.FLAG}:{m}")
            members[m] = theme.members_of(heat, top_n) if heat else {}
    out: dict[str, dict] = {}
    for r in rows:
        of = members.get(r["market"], {})
        themes: list[str] = []
        for t in json.loads(r["tickers_json"] or "[]"):
            themes += [th for th in of.get(t, []) if th not in themes]
        out[str(r["id"])] = {"catalyst": theme.is_catalyst(r["source"]), "themes": themes}
    return out


@router.get("/news/themes")
def news_themes(request: Request, market: str, n: int = Query(10, ge=1, le=30)) -> dict:
    """데몬 `<시장>_theme` 잡이 남긴 최신 결과(없으면 빈 목록). 상태 불변."""
    m = _market(market)
    deps = request.app.state.deps
    with closing(deps.conn_factory()) as conn:
        heat = get_flag(conn, f"{theme.FLAG}:{m}") or {}
    return {
        "market": m,
        "date": heat.get("date"),
        "observed_at": heat.get("observed_at"),
        "top_n": deps.settings.theme.top_n,
        "themes": theme.top(heat, n),
    }


class WatchlistBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tickers: list[str]


@router.get("/settings/watchlist")
def get_watchlist(request: Request) -> dict:
    with closing(request.app.state.deps.conn_factory()) as conn:
        return {"tickers": watchlist.get_watchlist(conn)}


@router.patch("/settings/watchlist", dependencies=WRITE)
def set_watchlist(request: Request, body: WatchlistBody) -> dict:
    """목록 전체를 바꾼다(형식 검증·대문자화·중복 제거). 바뀌면 control 이력에 남는다."""
    with closing(request.app.state.deps.conn_factory()) as conn:
        try:
            out = watchlist.set_watchlist(
                conn, body.tickers, channel="web", actor=actor_of(request)
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    return {"tickers": out}


def _theme_fp(deps: Any) -> str:
    """테마 열기 원장 건수·최대 id·최신 관측 시각."""
    with closing(deps.conn_factory()) as conn:
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(id), 0), COALESCE(MAX(observed_at), '') FROM theme_heat"
        ).fetchone()
    return ":".join(str(v) for v in tuple(row))


def _watchlist_fp(deps: Any) -> str:
    """저장된 관심종목 목록 그대로(같은 값 다시 쓰기는 지문이 같다)."""
    with closing(deps.conn_factory()) as conn:
        return ",".join(watchlist.get_watchlist(conn))


SSE_KEYS = (SseKey("theme", _theme_fp), SseKey("watchlist", _watchlist_fp))
