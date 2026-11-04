"""조회 자원 합치기: 여러 기능의 같은 경로 `ResourcePart` 를 `GET` 하나로 마운트한다.

응답은 `{"items": [...], "partial": bool}`. 조각 하나가 실패해도 나머지는 돌려주고
`partial=True` 로 표시한다. `items` 는 `at` 역순(없는 항목은 끝)."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from fastapi import FastAPI, Request

from trader.kernel.registry.types import Feature, ResourcePart

logger = logging.getLogger("trader.web")

PREFIX = "/api"


def _sort_key(item: dict) -> tuple[bool, str]:
    at = item.get("at")
    return (at is not None, str(at) if at is not None else "")


def _handler(path: str, parts: list[ResourcePart]):
    def merged(request: Request) -> dict:
        deps = request.app.state.deps
        q = {**request.query_params, **request.path_params}
        items: list[dict] = []
        partial = False
        for part in parts:
            try:
                items += part.fn(deps, dict(q))
            except Exception:
                logger.warning("merge 조각 실패: %s", path, exc_info=True)
                partial = True
        items.sort(key=_sort_key, reverse=True)
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
