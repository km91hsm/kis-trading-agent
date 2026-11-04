"""기능 레지스트리: Feature 계약 타입, 발견, 계약·import 경계 검사."""

from __future__ import annotations

from trader.kernel.registry.contracts import check, import_violations
from trader.kernel.registry.discover import discover
from trader.kernel.registry.types import (
    AgentDef,
    Feature,
    HealthArea,
    Hook,
    JobSpec,
    NavItem,
    OriginSpec,
    Port,
    QueueKind,
    RegistryError,
    ResourcePart,
    SessionKind,
    SseKey,
    StageRule,
    StatusSection,
    TelegramCallback,
    TelegramCommand,
    ToolCommand,
)

__all__ = [
    "AgentDef",
    "Feature",
    "HealthArea",
    "Hook",
    "JobSpec",
    "NavItem",
    "OriginSpec",
    "Port",
    "QueueKind",
    "RegistryError",
    "ResourcePart",
    "SessionKind",
    "SseKey",
    "StageRule",
    "StatusSection",
    "TelegramCallback",
    "TelegramCommand",
    "ToolCommand",
    "check",
    "discover",
    "import_violations",
]
