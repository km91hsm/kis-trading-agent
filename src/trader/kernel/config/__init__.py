"""설정 로더 + 섹션 레지스트리. 커널 자체 섹션(settings.markets·web·notify)만 여기서 등록한다."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from trader.kernel.config.errors import ConfigError
from trader.kernel.config.loader import load, memo_load
from trader.kernel.config.secrets import Secrets, account_prefix, load_secrets
from trader.kernel.config.sections import ConfigFile, Section, SectionRegistry
from trader.kernel.config.time import KST
from trader.kernel.ops.settings import OpsCheckSchedule, OpsSettings

Env = Literal["paper", "real"]

Market = Literal["KR", "US"]


class WebSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bind: str = "127.0.0.1"
    port: int = 8787
    # tailscale serve 로 들어오는 Host 이름(예: pc.tailnet.ts.net). 비면 루프백만 허용
    tailscale_hosts: list[str] = Field(default_factory=list)
    # 허용할 Tailscale-User-Login(소문자 비교). 비면 tailnet Host 만으로 허용
    tailscale_users: list[str] = Field(default_factory=list)
    # 폰에서 여는 외부 주소(예: https://pc.tailnet.ts.net). 없으면 None
    public_base: str | None = None


class NotifySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    poll_interval_s: int = Field(30, ge=5, le=600)


class UsageThrottle(BaseModel):
    """구독 사용률 감속. 마지막 관측(세션 rate_limit_event·상태줄 파일)의 5시간·7일 창 중
    하나라도 soft 이상이면 보유 재평가(resume)만, hard 이상이면 새 세션을 전부 막는다.
    관측이 없거나 max_age_h 보다 오래면 막지 않는다(고정 상한 daily_session_cap 만)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    soft_pct: float = Field(0.80, gt=0, le=1)
    hard_pct: float = Field(0.95, gt=0, le=1)
    max_age_h: float = Field(6.0, gt=0)


class UsageProbe(BaseModel):
    """구독 사용률 확인(웹 실시간): 데몬이 interval_min 마다 haiku 단발 `claude -p` 1회로 5시간·7일
    사용률과 초기화 시각을 관측한다. 최근 skip_if_recent_min 안에 다른 관측(세션)이 있으면 건너뛰고,
    사용률이 감속 hard 이상이면 확인도 쉰다(사용량을 더 쓰지 않게)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    interval_min: int = Field(30, ge=5, le=360)
    skip_if_recent_min: int = Field(5, ge=0, le=120)


class CodexDebateSettings(BaseModel):
    """다음 새 연구부터 적용되는 Codex bear 참여 설정."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    model: str | None = None
    timeout_s: int = Field(180, ge=1, le=1800)
    daily_call_cap: int = Field(80, ge=1, le=10000)
    bin: str | None = None
    # 사용량 조절: 켜져 있으면 새 연구 시작 때 Codex 5시간·7일 사용률이 hard_pct 이상이거나
    # 오늘 호출 상한에 닿았으면 그 연구는 Claude 강세·약세로 진행한다(꺼지면 호출 상한만 본다).
    usage_throttle: bool = True
    hard_pct: float = Field(0.95, gt=0, le=1)


class LlmSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codex_debate: CodexDebateSettings = Field(default_factory=CodexDebateSettings)
    budget_usd: float = 5.0
    # 세션 타임아웃(초). 2026-10-29 운영 점검: 600초에서 분석 세션 35% 가 시간 초과(성공 최장 588초)
    timeout_s: int = 900
    # 종류별 타임아웃(없으면 timeout_s). 스크리너·발굴은 짧게, 재평가는 중간
    timeout_by_kind: dict[str, int] = Field(
        default_factory=lambda: {
            "screen": 300, "discover": 300, "resume": 600, "fo_resume": 600, "reflect": 600,
            "market": 600,
        }
    )  # fmt: skip
    # 같은 운영일 같은 종목이 타임아웃으로 이만큼 실패하면 그날은 다시 띄우지 않는다
    timeout_retry_max: int = Field(default=2, ge=1, le=10)
    # 종목 analyze + 시장 세션 + 장중 발굴·재분석 합계
    daily_session_cap: int = 80
    usage_throttle: UsageThrottle = Field(default_factory=UsageThrottle)
    market_session_cap: int = 2  # 하루 시장 세션(kind=market) 상한 (종목 분석 예산 보호)
    # 대화형 Claude Code 상태줄이 남기는 구독 사용률 관측 파일. 비우면 표시 안 함
    usage_file: str | None = "~/.claude/rate_limits.json"
    usage_probe: UsageProbe = Field(default_factory=UsageProbe)

    def timeout_for(self, kind: str) -> int:
        return int(self.timeout_by_kind.get(kind, self.timeout_s))


class JobQueueSchedule(BaseModel):
    """schedule.yaml 의 job_queue: 데몬 큐 틱(4레인 공통) 주기."""

    model_config = ConfigDict(extra="forbid")

    interval_s: int = Field(default=10, ge=1)


class TelegramPollSchedule(BaseModel):
    """schedule.yaml 의 telegram_poll: 내장 텔레그램 폴링 주기."""

    model_config = ConfigDict(extra="forbid")

    interval_s: int = Field(default=30, ge=1)


KERNEL_SECTIONS: tuple[Section, ...] = (
    Section("settings", "markets", list[Market], lambda: ["KR", "US"]),
    Section("settings", "web", WebSettings, WebSettings),
    Section("settings", "notify", NotifySettings, NotifySettings),
    Section("settings", "llm", LlmSettings, LlmSettings),
    Section("settings", "models", dict[str, str], dict),
    Section("settings", "claude_bin", str | None, lambda: None),
    Section("settings", "anonymize", bool, lambda: False),
    Section("settings", "ops", OpsSettings, OpsSettings),
    Section("schedule", "job_queue", JobQueueSchedule, JobQueueSchedule),
    Section("schedule", "telegram_poll", TelegramPollSchedule, TelegramPollSchedule),
    Section("schedule", "ops_check", OpsCheckSchedule, OpsCheckSchedule),
    Section("schedule", "ledger_backup", str, lambda: "17:10"),
    Section("schedule", "retention", str, lambda: "17:20"),
)


def kernel_registry() -> SectionRegistry:
    """커널 섹션만 담은 새 레지스트리 (부트가 copy 해 기능 섹션을 더한다)."""
    reg = SectionRegistry()
    for section in KERNEL_SECTIONS:
        reg.register(section)
    return reg


__all__ = [
    "KERNEL_SECTIONS",
    "KST",
    "ConfigError",
    "ConfigFile",
    "Env",
    "JobQueueSchedule",
    "LlmSettings",
    "Market",
    "NotifySettings",
    "Section",
    "SectionRegistry",
    "Secrets",
    "UsageProbe",
    "UsageThrottle",
    "WebSettings",
    "account_prefix",
    "kernel_registry",
    "load",
    "load_secrets",
    "memo_load",
]
