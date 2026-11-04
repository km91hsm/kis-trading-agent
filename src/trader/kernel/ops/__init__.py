"""커널 운영 점검(health)·영역 공개면.

이름은 처음 쓸 때 가져온다(PEP 562) — `config` 가 `ops.settings` 를, `registry.contracts` 가
`ops.types` 를 가져올 때 영역 모듈(브로커·원장·세션)까지 끌려와 순환하지 않게."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from trader.kernel.ops.areas import KERNEL_AREAS
    from trader.kernel.ops.health import (
        LABEL,
        LEVELS,
        WEB_LEVEL,
        check,
        escalate,
        headline,
        latest,
        store,
        to_web,
        worst,
    )
    from trader.kernel.ops.types import Item, OpsContext

_HOME = {
    "KERNEL_AREAS": "areas",
    "LABEL": "health",
    "LEVELS": "health",
    "WEB_LEVEL": "health",
    "Item": "types",
    "OpsContext": "types",
    "check": "health",
    "escalate": "health",
    "headline": "health",
    "latest": "health",
    "store": "health",
    "to_web": "health",
    "worst": "health",
}

__all__ = [
    "KERNEL_AREAS",
    "LABEL",
    "LEVELS",
    "WEB_LEVEL",
    "Item",
    "OpsContext",
    "check",
    "escalate",
    "headline",
    "latest",
    "store",
    "to_web",
    "worst",
]


def __getattr__(name: str) -> Any:
    home = _HOME.get(name)
    if home is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(f"{__name__}.{home}"), name)
