"""기능 레지스트리 계약 타입. 기능은 `features/<x>/feature.py` 의 `FEATURE` 하나로 등록한다."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import typer
from fastapi import APIRouter

from trader.kernel.config.sections import Section
from trader.kernel.ledger.migrations import Migration


class RegistryError(Exception):
    """기능 발견·등록 계약 위반."""


@dataclass(frozen=True)
class JobSpec:
    """데몬이 순회해 등록하는 스케줄 잡 한 개."""

    id: str
    kind: str
    trigger: Literal["cron", "interval"]
    per_market: bool
    schedule_key: str | None
    interval_s: int | None
    enabled: Callable[[Any], bool] | None
    gate: Literal["market", "monitor", "none"]
    heavy: bool
    misfire: Literal["scan", "idempotent", "default"]
    catch_up: Literal["queue", "direct", "notify", "none"]
    start_soon_s: int
    body: Callable[..., Any]
    teardown: Callable[..., None] | None = None


@dataclass(frozen=True)
class QueueKind:
    """잡 큐가 처리하는 요청 종류."""

    kind: str
    params_model: type
    label: str
    lane: Literal["slow", "fast", "reeval", "info"]
    priority: int
    trading: bool
    restart_requeue: bool
    body: Callable[..., Any]


@dataclass(frozen=True)
class SseKey:
    """SSE 지문 키: probe 결과가 바뀌면 프런트가 갱신한다. noisy 는 잦은 변화 표시."""

    key: str
    probe: Callable[[Any], Any]
    noisy: bool = False


@dataclass(frozen=True)
class OriginSpec:
    """주문 실행 출처 선언."""

    origin: str
    risk_stage: str
    order_stage: str
    has_decision_row: bool
    gate_exemptions: frozenset[str]
    idem: Literal["decision", "stage", "leg"]


@dataclass(frozen=True)
class SessionKind:
    """LLM 세션 종류."""

    kind: str
    agents: tuple[str, ...]
    prompt_section: str
    schemas: tuple[type, ...]
    timeout_key: str
    runs_kind: str
    extra_cap_key: str | None
    throttle: Literal["hard", "soft"]
    fake: Callable[..., Any]
    # spec -> stdin 에 더할 키(없으면 기본 페이로드만)
    stdin_extra: Callable[[Any], dict] | None = None
    # True 면 같은 운영일 같은 종목이 세션 타임아웃으로 `llm.timeout_retry_max` 번 실패한 뒤
    # 다시 띄우지 않는다(단일 종목 세션만). 커널은 세션 이름이 아니라 이 선언만 본다
    retry_guard: bool = False


@dataclass(frozen=True)
class TelegramCommand:
    name: str
    untrusted_ok: bool
    help_line: str
    # (CommandContext, arg: str | None) -> str — 돌려준 문자열을 디스패처가 보낸다
    handler: Callable[..., Any]


@dataclass(frozen=True)
class StageRule:
    """도구 명령이 허용되는 단계 전제조건.

    pattern: 쓸 수 있는 단계 이름 glob. requires: 이미 있어야 하는 정확한 단계 이름.
    min_counts: (glob, n) — glob 에 맞는 기존 단계가 n 개 이상이어야 한다."""

    pattern: str
    requires: tuple[str, ...] = ()
    min_counts: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class ToolCommand:
    """`trader tool <name>`. handler 가 typer.Typer 면 하위 명령 그룹, callable 이면 단일 명령."""

    name: str
    handler: Any
    stage_rules: tuple[StageRule, ...] = ()


@dataclass(frozen=True)
class HealthArea:
    key: str
    check: Callable[..., Any]


@dataclass(frozen=True)
class StatusSection:
    title: str
    # (CommandContext) -> list[str] — /status 본문 줄
    render: Callable[..., Any]


@dataclass(frozen=True)
class Hook:
    """실패 격리되는 단계 훅."""

    point: Literal[
        "scan_analyze.pre",
        "scan_analyze.post",
        "monitor.pre_stop",
        "news.post_ingest",
        "reflect.mature",
        "daemon.bootstrap",
    ]
    fn: Callable[..., Any]


@dataclass(frozen=True)
class AgentDef:
    """LLM 에이전트 정의 파일 하나. schema: 그 에이전트 결과의 pydantic 모델(없으면 None)."""

    name: str
    path: Path
    schema: type | None = None


@dataclass(frozen=True)
class Port:
    """부트가 조립하는 이름 붙은 구현체. build(boot) -> 구현체."""

    name: str
    build: Callable[[Any], Any]


@dataclass(frozen=True)
class Feature:
    """기능 하나의 전체 등록 선언. 커널은 이 필드를 순회할 뿐 기능 이름을 모른다."""

    id: str
    tier: Literal["service", "lane"]
    sections: tuple[Section, ...] = ()
    migrations: tuple[Migration, ...] = ()
    jobs: tuple[JobSpec, ...] = ()
    hooks: tuple[Hook, ...] = ()
    queue_kinds: tuple[QueueKind, ...] = ()
    cli: typer.Typer | None = None
    tools: tuple[ToolCommand, ...] = ()
    routes: tuple[APIRouter, ...] = ()
    sse_keys: tuple[SseKey, ...] = ()
    nav: tuple[dict, ...] = ()
    telegram_commands: tuple[TelegramCommand, ...] = ()
    health_areas: tuple[HealthArea, ...] = ()
    status_sections: tuple[StatusSection, ...] = ()
    control_namespaces: tuple[str, ...] = ()
    origins: tuple[OriginSpec, ...] = ()
    session_kinds: tuple[SessionKind, ...] = ()
    agents: tuple[AgentDef, ...] = ()
    ports: tuple[Port, ...] = ()
