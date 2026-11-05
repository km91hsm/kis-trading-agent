"""accounts.yaml 모델과 로더 (옛 trader.config 의 AccountConfig·load_accounts 이식)."""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trader.kernel.broker.models import BrokerAccount
from trader.kernel.config import ConfigError, Env, Market, Section, SectionRegistry, load
from trader.kernel.config.secrets import CRED_PREFIX_RE, Secrets, load_secrets
from trader.kernel.config.time import HM_PATTERN

logger = logging.getLogger("trader.config")

Mode = Literal["auto", "approve", "off"]
Asset = Literal["stock", "fo"]

CANO_PLACEHOLDER = "00000000"


class Account(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    env: Env
    cano: str
    acnt_prdt_cd: str = "01"
    markets: list[Market] = Field(default_factory=lambda: ["KR", "US"])
    mode: Mode
    initial_capital: Decimal | None = None
    # 실전 자본 상한: 시장(통화)별로 사이징에 쓰는 NAV·가능금액을 이 금액까지로 자른다.
    # 사람이 단계적으로 올린다(자동 증액 없음). 없으면 상한 없음
    capital_cap: dict[Market, Decimal] | None = None
    # 계좌 전용 키 접두사(KIS 는 앱키를 계좌마다 발급한다). 없으면 계좌 id 로 정한
    # `KIS_<ID>_`(real-2 → KIS_REAL_2_) 에 APP_KEY 가 있을 때 그것을, 그것도 없으면 env 공용 키
    credentials: str | None = None
    # 계좌 자산군: stock 은 현물 파이프라인, fo 는 선물옵션 레인. `split_accounts` 로 가른다
    asset: Asset = "stock"

    @model_validator(mode="after")
    def _fo_markets(self):
        if self.asset == "fo" and self.markets != ["KR"]:
            raise ValueError(f"asset fo 계좌 {self.id!r} 의 markets 는 [KR] 만 가능합니다")
        return self

    @field_validator("credentials")
    @classmethod
    def _check_prefix(cls, v: str | None) -> str | None:
        if v is not None and not CRED_PREFIX_RE.match(v):
            raise ValueError(f"credentials 는 KIS_ 로 시작해 _ 로 끝나는 대문자 접두사: {v!r}")
        return v


SECTION = Section("accounts", "accounts", list[Account], list)


class MonitorWindow(BaseModel):
    """schedule.yaml `<시장>.monitor`: 장중 감시 주기(초)와 시장 현지 시각 창."""

    model_config = ConfigDict(extra="forbid")

    interval_s: int = Field(default=60, ge=1)
    start: str = Field(default="09:00", pattern=HM_PATTERN.pattern)
    end: str = Field(default="15:30", pattern=HM_PATTERN.pattern)


class KrSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monitor: MonitorWindow = Field(default_factory=MonitorWindow)


class UsSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monitor: MonitorWindow = Field(
        default_factory=lambda: MonitorWindow(start="09:30", end="16:00")
    )


SCHEDULE_SECTIONS: tuple[Section, ...] = (
    Section("schedule", "kr", KrSchedule, KrSchedule),
    Section("schedule", "us", UsSchedule, UsSchedule),
)


def _registry() -> SectionRegistry:
    reg = SectionRegistry()
    reg.register(SECTION)
    return reg


def _cano_from_secrets(a: Account, secrets: Secrets | None) -> Account:
    """cano 가 자리표시자면 `KIS_<PAPER|REAL>_CANO` 로 채운다(없으면 그대로 두고 경고)."""
    if a.cano and a.cano != CANO_PLACEHOLDER and not a.cano.startswith("${"):
        return a
    creds = secrets.kis_creds(a) if secrets is not None else None
    key = (creds.prefix if creds is not None else f"KIS_{a.env.upper()}_") + "CANO"
    # 전용 키 묶음을 골랐으면 그 접두사의 계좌번호만 — 다른 계좌의 공용 번호를 빌려 쓰지 않는다
    value = (creds.cano if creds is not None else None) or ""
    if not value:
        logger.warning("계좌 %s 의 계좌번호가 자리표시자이고 secrets %s 도 비어 있음", a.id, key)
        return a
    return a.model_copy(update={"cano": value})


def load_accounts(path: Path | None = None, *, secrets: Secrets | None = None) -> list[Account]:
    """accounts.yaml → 계좌 목록. cano 자리표시자는 secrets(.env) 의 `KIS_*_CANO` 로 채운다
    (secrets 를 넘기지 않으면 자리표시자가 있을 때만 `load_secrets()` 를 읽는다)."""
    accounts: list[Account] = load("accounts", _registry(), path).accounts  # type: ignore[attr-defined]
    if any(not a.cano or a.cano == CANO_PLACEHOLDER or a.cano.startswith("${") for a in accounts):
        if secrets is None:
            secrets = load_secrets()
        accounts = [_cano_from_secrets(a, secrets) for a in accounts]
    seen: set[str] = set()
    for a in accounts:
        if a.id in seen:
            raise ConfigError(f"accounts.yaml: 계좌 id 중복: {a.id}")
        seen.add(a.id)
        if a.env == "real" and a.mode == "auto":
            raise ConfigError(
                f"accounts.yaml: 실전 계좌 '{a.id}' 는 yaml 에서 auto 로 둘 수 없습니다 "
                "(런타임 control 에서 확인 문자열로만 전환)"
            )
    return accounts


def split_accounts(accounts: list[Account]) -> tuple[list[Account], list[Account]]:
    """계좌 목록을 (현물, 선물옵션) 으로 가른다(순서 유지). 현물 소비자는 첫 목록만 받는다."""
    stock = [a for a in accounts if a.asset == "stock"]
    fo = [a for a in accounts if a.asset == "fo"]
    return stock, fo


def to_broker_account(a: Account) -> BrokerAccount:
    """커널 브로커가 아는 필드만 복사한다(mode·자본 등은 넘기지 않는다)."""
    return BrokerAccount(
        id=a.id,
        env=a.env,
        cano=a.cano,
        acnt_prdt_cd=a.acnt_prdt_cd,
        markets=list(a.markets),
        asset=a.asset,
    )
