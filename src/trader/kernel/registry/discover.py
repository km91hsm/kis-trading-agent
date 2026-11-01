"""기능 발견: 커널이 기능 패키지를 동적으로 import 하는 유일한 곳(정적 import 금지)."""

from __future__ import annotations

import importlib
import logging
import pkgutil

from trader.kernel.registry.types import Feature, RegistryError

log = logging.getLogger("trader.registry")


def discover(package: str = "trader.features") -> list[Feature]:
    """package 의 하위 패키지마다 `<pkg>.feature.FEATURE` 를 모아 id 순으로 돌려준다.

    feature 모듈이나 FEATURE 가 없는 하위 패키지는 RegistryError."""
    root = importlib.import_module(package)
    found: list[Feature] = []
    for info in pkgutil.iter_modules(root.__path__):
        if not info.ispkg:
            continue
        name = f"{package}.{info.name}.feature"
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name != name:
                raise
            raise RegistryError(f"기능 {info.name}: feature.py 가 없습니다 ({name})") from exc
        feature = getattr(module, "FEATURE", None)
        if not isinstance(feature, Feature):
            raise RegistryError(f"기능 {info.name}: {name} 에 FEATURE(Feature) 가 없습니다")
        log.debug("기능 발견: %s", feature.id)
        found.append(feature)
    return sorted(found, key=lambda f: f.id)
