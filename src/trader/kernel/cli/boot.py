"""명령 공통 부트스트랩: 로깅 → 기능 발견 → 계약 검사 → 설정 레지스트리 → (요청 시) 설정·원장."""

from __future__ import annotations

import functools
import logging
import sqlite3
from dataclasses import dataclass, field, replace
from typing import Any

import typer

from trader.kernel.config import (
    ConfigError,
    SectionRegistry,
    kernel_registry,
    load_secrets,
    memo_load,
)
from trader.kernel.ledger import db
from trader.kernel.platform import logging_setup, paths
from trader.kernel.registry import RegistryError, check, discover
from trader.kernel.registry.contracts import all_migrations
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
    secrets: Any | None = None
    ports: dict[str, Any] = field(default_factory=dict)


def build_registry(features: list[Feature]) -> SectionRegistry:
    """커널 섹션 + 모든 기능 섹션을 담은 설정 레지스트리."""
    registry = kernel_registry().copy()
    for f in features:
        for section in f.sections:
            registry.register(section)
    return registry


def boot(
    *,
    settings: bool = False,
    conn: bool = False,
    schedule: bool = False,
    secrets: bool = False,
    ports: bool = False,
    log_name: str | None = None,
) -> Boot:
    """로깅 → discover → 계약 검사(위반이면 ConfigError) → 레지스트리 → 설정 → 원장 → 포트 순서.

    ports=True 는 settings·secrets·conn 을 함의하고, 모든 Feature.ports 를 이름으로 조립한다."""
    if ports:
        settings = secrets = conn = True
    logging_setup.configure(paths.state_dir(), name=log_name)
    features = discover()
    errs = check(features)
    if errs:
        raise ConfigError("\n".join(errs))
    registry = build_registry(features)
    settings_obj = memo_load("settings", registry) if settings else None
    schedule_obj = memo_load("schedule", registry) if schedule else None
    secrets_obj = load_secrets() if secrets else None
    connection = None
    if conn:
        connection = db.connect()
        db.init_schema(connection, all_migrations(features))
    boot_ = Boot(settings_obj, connection, features, schedule_obj, registry, secrets_obj)
    if not ports:
        return boot_
    # 모든 포트가 같은 ports dict 를 받는다 — 먼저 조립된 포트가 나중 포트를 지연 해석할 수 있다
    built: dict[str, Any] = {}
    boot_ = replace(boot_, ports=built)
    for f in features:
        for port in f.ports:
            try:
                built[port.name] = port.build(boot_)
            except Exception as e:  # noqa: BLE001 - 어떤 조립 실패도 설정 오류로 알린다
                if connection is not None:
                    connection.close()
                raise ConfigError(
                    f"포트 {port.name} 조립 실패: {type(e).__name__}: {e}"
                ) from e
    return boot_


def config_errors(fn):
    """설정·프로젝트 루트 오류를 '오류: …' 한 줄과 exit 1 로 바꾼다. 시그니처를 보존한다."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ConfigError, RegistryError, paths.ProjectRootNotFound) as e:
            typer.echo(f"오류: {e}", err=True)
            raise typer.Exit(code=1) from e

    return wrapper
