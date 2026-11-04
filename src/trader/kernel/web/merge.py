"""조회 자원 합치기: 여러 기능의 같은 경로 `ResourcePart` 를 `GET` 하나로 마운트한다.

응답은 `{"items": [...], "partial": bool}`. 조각 하나가 실패하거나 목록이 아닌 값을 내도 나머지는
돌려주고 `partial=True` 로 표시한다. `items` 는 `at` 시각 역순(ISO8601 로 비교 — 날짜만이면 그날
00:00 UTC, 오프셋 없으면 UTC; 없거나 못 읽는 항목은 끝). `?format=csv` 면 같은 items 를 CSV 로."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, datetime

from fastapi import FastAPI, Request

from trader.kernel.registry.types import Feature, ResourcePart
from trader.kernel.web.csv import csv_response

logger = logging.getLogger("trader.web")

PREFIX = "/api"


_FLOOR = datetime.min.replace(tzinfo=UTC)


def _parse_at(at: object) -> datetime | None:
    """`at` → UTC aware 시각. 날짜만은 그날 00:00 UTC, naive 는 UTC. 못 읽으면 None."""
    if at is None:
        return None
    try:
        dt = datetime.fromisoformat(str(at))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _sort_key(item: dict) -> tuple[bool, datetime]:
    dt = _parse_at(item.get("at"))
    return (dt is not None, dt or _FLOOR)


def _handler(path: str, parts: list[ResourcePart]):
    filename = (path.rstrip("/").rsplit("/", 1)[-1].strip("{}") or "items") + ".csv"

    def merged(request: Request):
        deps = request.app.state.deps
        q = {**request.query_params, **request.path_params}
        items: list[dict] = []
        partial = False
        for part in parts:
            try:
                got = part.fn(deps, dict(q))
            except Exception:
                logger.warning("merge 조각 실패: %s", path, exc_info=True)
                partial = True
                continue
            if not isinstance(got, list):
                logger.warning("merge 조각이 목록이 아님(%s): %s", type(got).__name__, path)
                partial = True
                continue
            items += got
        items.sort(key=_sort_key, reverse=True)
        if q.get("format") == "csv":
            # 조각마다 열이 달라(현물·파생) 첫 행이 아니라 모든 행의 키 합집합(처음 나온 순)
            return csv_response(items, filename, list(dict.fromkeys(k for r in items for k in r)))
        return {"items": items, "partial": partial}

    return merged


def mount(app: FastAPI, features: Iterable[Feature]) -> None:
    """같은 `path` 의 part 들을 기능 순서대로 묶어 `/api<path>` GET 하나로 건다."""
    by_path: dict[str, list[ResourcePart]] = {}
    for feature in features:
        for part in feature.resources:
            by_path.setdefault(part.path, []).append(part)
    for path, parts in by_path.items():
        app.add_api_route(PREFIX + path, _handler(path, parts), methods=["GET"], tags=["merge"])
