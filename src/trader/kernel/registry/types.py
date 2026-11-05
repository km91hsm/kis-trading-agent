"""기능 레지스트리 계약 타입. 기능은 `features/<x>/feature.py` 의 `FEATURE` 하나로 등록한다."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import typer

from trader.kernel.config.sections import Section
from trader.kernel.ledger.migrations import Migration

if TYPE_CHECKING:  # 순환 import 방지(notify·web 이 이 모듈을 import), fastapi 는 경량 기동(P19)
    from fastapi import APIRouter

    from trader.kernel.notify.commands import CommandContext
    from trader.kernel.web.deps import WebDeps


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
    # cron 요일 제한(APScheduler day_of_week 표기, 예: "mon-sat"). None 이면 매일
    days: str | None = None


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
class TelegramCallback:
    """인라인 버튼 콜백: `callback_data` 가 prefix 로 시작하면 handler(ctx, data) 결과를 보낸다."""

    prefix: str
    handler: Callable[[CommandContext, str], str]


@dataclass(frozen=True)
class ResourcePart:
    """여러 기능이 한 API 경로에 조각을 보태는 조회 자원. 커널 `web.merge` 가 같은 path 를
    GET 하나로 합친다. fn(deps, q) — q 는 쿼리 + 경로 파라미터, 반환은 행 dict 목록."""

    path: str
    fn: Callable[[WebDeps, dict[str, str]], list[dict]]


@dataclass(frozen=True)
class NavItem:
    """웹 메뉴 항목: 커널 고정 화면(screen) 아래 하위 목차. order 오름차순으로 놓인다."""

    screen: Literal["today", "accounts", "performance", "data", "system"]
    id: str
    label: str
    order: int


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
    """운영 점검 영역 하나. check: `(kernel.ops.OpsContext) -> list[kernel.ops.Item]`.

    기능 영역은 기능당 하나, key = feature id(계약 검사가 중복·불일치를 막는다). 커널 내장 영역은
    `jobs`·`llm`·`orders`. 항목 key 는 `"{key}.{세부}"` — 접두가 없으면 러너가 붙이고 경고 로그를
    남긴다. check 가 예외를 던지면 러너가 그 영역만 `"{key}.error"` warning 1건으로 바꾼다."""

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
class GateSpec:
    """데몬 게이트 공급자. name 은 JobSpec.gate 가 가리키는 이름(market|monitor).

    build(boot, deps) -> gate(job) -> 건너뜀 사유 또는 None. 러너는 `gate(job)` 만 부르므로,
    게이트는 deps(now·conn_factory·ports·schedule)를 build 시점에 붙잡는다. `build_daemon_deps`
    가 deps 를 gates={} 로 먼저 만든 뒤 `deps.gates[name] = build(boot, deps)` 로 채운다."""

    name: Literal["market", "monitor"]
    build: Callable[[Any, Any], Callable[[Any], str | None]]


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
    resources: tuple[ResourcePart, ...] = ()
    nav: tuple[NavItem, ...] = ()
    telegram_commands: tuple[TelegramCommand, ...] = ()
    telegram_callbacks: tuple[TelegramCallback, ...] = ()
    health_areas: tuple[HealthArea, ...] = ()
    status_sections: tuple[StatusSection, ...] = ()
    control_namespaces: tuple[str, ...] = ()
    origins: tuple[OriginSpec, ...] = ()
    session_kinds: tuple[SessionKind, ...] = ()
    agents: tuple[AgentDef, ...] = ()
    ports: tuple[Port, ...] = ()
    gates: tuple[GateSpec, ...] = ()
