"""설정 로더: 등록된 섹션으로 조립한 모델로 yaml 을 검증한다."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from trader.kernel.config.errors import ConfigError
from trader.kernel.config.sections import ConfigFile, SectionRegistry
from trader.kernel.config.yaml_io import read_yaml
from trader.kernel.platform import paths


def _describe(e: ValidationError) -> str:
    parts = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "(루트)"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


def load(file: ConfigFile, registry: SectionRegistry, path: Path | None = None) -> BaseModel:
    """`<config_dir>/<file>.yaml` (또는 path) 을 읽어 검증한다.

    파일이 없으면 모든 섹션에 default 가 있을 때만 기본값으로 만든다."""
    p = path or paths.config_dir() / f"{file}.yaml"
    name = f"{file}.yaml"
    data = read_yaml(p)
    if not p.exists() and any(s.default is None for s in registry.sections(file)):
        raise ConfigError(f"{name} 파일이 없습니다: {p}")
    known = set(registry.keys(file))
    for key in data:
        if key not in known:
            raise ConfigError(f"{name}: 알 수 없는 키 {key}")
    try:
        return registry.model_for(file).model_validate(data)
    except ValidationError as e:
        raise ConfigError(f"{name} 검증 실패: {_describe(e)}") from e


_MEMO: dict[tuple[str, str], tuple[tuple, Any, BaseModel]] = {}
_MEMO_LOCK = threading.Lock()


def _file_sig(p: Path) -> tuple:
    """파일 변경 감지 값(mtime_ns·크기). 없으면 ('missing',)."""
    try:
        st = p.stat()
    except OSError:
        return ("missing",)
    return (st.st_mtime_ns, st.st_size)


def memo_load(file: ConfigFile, registry: SectionRegistry, path: Path | None = None) -> BaseModel:
    """load 결과를 (경로, 파일) 별로 기억한다. 파일 mtime·크기나 등록 모델이 바뀌면 다시 읽고,
    검증 오류는 기억하지 않는다(고치면 바로 반영)."""
    p = path or paths.config_dir() / f"{file}.yaml"
    key = (str(p.resolve()), file)
    sig = _file_sig(p)
    model = registry.model_for(file)
    with _MEMO_LOCK:
        hit = _MEMO.get(key)
        if hit is not None and hit[0] == sig and hit[1] is model:
            return hit[2]
    obj = load(file, registry, p)
    with _MEMO_LOCK:
        _MEMO[key] = (sig, model, obj)
    return obj
