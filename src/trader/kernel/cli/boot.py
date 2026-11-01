"""명령 공통 부트스트랩: 로깅 → 기능 발견 → 계약 검사 → 설정 레지스트리 → (요청 시) 설정·원장."""

from __future__ import annotations

import functools
import logging
import sqlite3
from dataclasses import dataclass
from typing import Any

import typer

from trader.kernel.config import ConfigError, SectionRegistry, kernel_registry, memo_load
from trader.kernel.ledger import db
from trader.kernel.ledger.migrations import KERNEL
from trader.kernel.platform import logging_setup, paths
from trader.kernel.registry import check, discover
from trader.kernel.registry.types import Feature

logger = logging.getLogger("trader.cli")


@dataclass(frozen=True)
class Boot:
    """부트 결과. conn 은 호출자가 닫는다."""

    settings: Any | None
    conn: sqlite3.Connection | None
    features: list[Feature]
    schedule: Any | None = None
    registry: SectionRegistry | None = None


def boot(
    *,
    settings: bool = False,
    conn: bool = False,
    schedule: bool = False,
    log_name: str | None = None,
) -> Boot:
    """로깅 → discover → 계약 검사(위반이면 ConfigError) → 레지스트리 → 설정 → 원장 순서."""
    logging_setup.configure(paths.state_dir(), name=log_name or "trader")
    features = discover()
    errs = check(features)
    if errs:
        raise ConfigError("\n".join(errs))
    registry = kernel_registry().copy()
    for f in features:
        for section in f.sections:
            registry.register(section)
    settings_obj = memo_load("settings", registry) if settings else None
    schedule_obj = memo_load("schedule", registry) if schedule else None
    connection = None
    if conn:
        connection = db.connect()
        migs = list(KERNEL) + [m for f in features for m in f.migrations]
        db.init_schema(connection, migs)
    return Boot(settings_obj, connection, features, schedule_obj, registry)


def config_errors(fn):
    """설정·프로젝트 루트 오류를 '오류: …' 한 줄과 exit 1 로 바꾼다. 시그니처를 보존한다."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ConfigError, paths.ProjectRootNotFound) as e:
            typer.echo(f"오류: {e}", err=True)
            raise typer.Exit(code=1) from e

    return wrapper
