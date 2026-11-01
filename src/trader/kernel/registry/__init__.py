"""기능 레지스트리: Feature 계약 타입, 발견, 계약·import 경계 검사."""

from __future__ import annotations

from trader.kernel.registry.contracts import check, import_violations
from trader.kernel.registry.discover import discover
from trader.kernel.registry.types import (
    Feature,
    HealthArea,
    Hook,
    JobSpec,
    OriginSpec,
    QueueKind,
    RegistryError,
    SessionKind,
    SseKey,
    StageRule,
    StatusSection,
    TelegramCommand,
    ToolCommand,
)

__all__ = [
    "Feature",
    "HealthArea",
    "Hook",
    "JobSpec",
    "OriginSpec",
    "QueueKind",
    "RegistryError",
    "SessionKind",
    "SseKey",
    "StageRule",
    "StatusSection",
    "TelegramCommand",
    "ToolCommand",
    "check",
    "discover",
    "import_violations",
]
