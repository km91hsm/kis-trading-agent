"""fo 설정: settings.yaml 의 `fo`, schedule.yaml 의 `fo`.

옛 `trader.config` 의 `FoCfg` 와 하위 모델을 필드·기본값·제약·검증기 그대로 옮겼다. 상품 모델은
커널 `broker.models.FoProduct` 를 쓴다(옛 `FoProductCfg`). yaml 키를 더하면
`templates/config/*.yaml` 도 같이 바꾼다."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trader.kernel.broker.models import FoProduct
from trader.kernel.config.sections import Section
from trader.kernel.config.time import check_hm

FoVehicle = Literal["FUTURES", "OPTION_BUY", "OPTION_SELL"]
Regime = Literal["bull", "neutral", "bear"]

class FoEntryWindowCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: str = "09:00"
    cutoff: str = "15:30"

    @field_validator("start", "cutoff")
    @classmethod
    def _v(cls, v: str) -> str:
        return check_hm(v)


class FoStressCfg(BaseModel):
    """시나리오 격자: 지수 ±index_pct × IV 상대 변화. 옵션은 Black-76 재평가."""

    model_config = ConfigDict(extra="forbid")

    index_pct: list[float] = Field(default_factory=lambda: [0.03, 0.06])
    iv_rel: list[float] = Field(default_factory=lambda: [0.0, 0.5])


class FoMarginRateCfg(BaseModel):
    """KRX 공표 위탁증거금률(사람이 출처·날짜와 함께 적는다). 상한이 아니라 주문가능 응답의
    정합성 검사에만 쓰고, asof 가 max_age_days 를 넘으면 선물·옵션 매도 신규를 거부한다."""

    model_config = ConfigDict(extra="forbid")

    futures: float = Field(default=0.12, gt=0, le=1)
    option_sell: float = Field(default=0.15, gt=0, le=1)
    asof: date = date(2026, 10, 27)
    max_age_days: int = Field(default=30, ge=1, le=365)


class FoSellStopMultCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min: float = Field(default=1.5, gt=1)
    default: float = Field(default=2.0, gt=1)
    max: float = Field(default=3.0, gt=1)

    @model_validator(mode="after")
    def _order(self):
        if not (self.min <= self.default <= self.max):
            raise ValueError("sell_stop_mult 는 min ≤ default ≤ max 여야 합니다")
        return self


class FoOptionCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_premium_pct: float = Field(default=0.05, gt=0, le=0.5)  # 옵션 매수 전액 ≤ NAV 비중
    buy_stop_pct: float = Field(default=0.5, gt=0, lt=1)  # 매수 프리미엄 손절(진입가 대비)
    sell_stop_mult: FoSellStopMultCfg = Field(default_factory=FoSellStopMultCfg)
    sell_max_notional_pct: float = Field(default=1.0, gt=0, le=5)  # 매도 비조정 명목 ≤ NAV 배
    sell_against_regime: bool = False  # 국면 역행 옵션 매도 허용(기본 거부)
    min_days_left: int = Field(default=5, ge=1, le=60)
    allowed_strikes: list[Literal["ATM", "OTM1", "OTM2", "ITM1"]] = Field(
        default_factory=lambda: ["ATM", "OTM1", "OTM2", "ITM1"]
    )
    weekly: bool = False


class FoReevalCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interval_min: int = Field(default=30, ge=1, le=390)
    max_per_day: int = Field(default=6, ge=1, le=50)
    periodic_min: int = Field(default=60, ge=5, le=390)
    periodic_max_per_day: int = Field(default=4, ge=0, le=50)
    tp_near_pct: float = Field(default=0.002, gt=0, le=0.1)
    iv_spike_pct: float = Field(default=0.3, gt=0, le=5)


class FoCfg(BaseModel):
    """선물옵션 방향성 레인(Phase 26, 모의 전용·기본 끔). `enabled` 는 신규 진입만 막는다 —
    보유분 감시·축소는 계속하고 꺼지면 살아 있는 진입 다리를 회수한다. 배수는 LLM 이 결정마다
    정하되 `max_leverage`·국면 상한·대조 계수 안으로 파이썬이 자른다."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    product: str = "mini"
    vehicles: list[FoVehicle] = Field(
        default_factory=lambda: ["FUTURES", "OPTION_BUY", "OPTION_SELL"]
    )
    hold_overnight: bool = True
    entry_window: FoEntryWindowCfg = Field(default_factory=FoEntryWindowCfg)
    max_input_age_s: int = Field(default=120, ge=10, le=3600)
    max_quote_age_s: int = Field(default=60, ge=5, le=3600)
    max_leverage: float = Field(default=2.0, ge=0.1, le=5)
    # 국면별 배수 상한: null(미설정) 은 max_leverage, 0 은 그 국면 신규 금지 — `or` 로 합치지 않는다
    leverage_by_regime: dict[Regime, float | None] = Field(default_factory=dict)
    max_contracts: int = Field(default=4, ge=1, le=100)
    max_positions: int = Field(default=1, ge=1, le=1)  # 상계·반대 보유 없음이 증거금 추론의 전제
    risk_per_trade_pct: float = Field(default=0.02, gt=0, le=0.2)  # 포지션 수명 손실 예산
    overnight_risk_pct: float = Field(default=0.05, gt=0, le=0.5)  # 시나리오 최악 손실 ≤ NAV 비중
    stress: FoStressCfg = Field(default_factory=FoStressCfg)
    margin_rate: FoMarginRateCfg = Field(default_factory=FoMarginRateCfg)
    margin_buffer_pct: float = Field(default=0.3, ge=0, lt=1)
    daily_loss_stop_pct: float = Field(default=0.03, gt=0, le=0.5)
    min_stop_pt: float = Field(default=0.5, gt=0)
    slippage_pt: float = Field(default=0.1, ge=0)
    min_confidence: float = Field(default=0.6, ge=0, le=1)
    roll_days: int = Field(default=2, ge=0, le=10)  # D-roll 강제 청산·신규 금지(옵션 매도 +1)
    close_before_min: int = Field(default=10, ge=1, le=120)
    exit_retry_max: int = Field(default=3, ge=1, le=10)
    cross_check: Literal["off", "tilt", "block"] = "tilt"
    session_cap: int = Field(default=7, ge=1, le=50)
    reeval: FoReevalCfg = Field(default_factory=FoReevalCfg)
    option: FoOptionCfg = Field(default_factory=FoOptionCfg)
    night_session: bool = False
    costs_bp: dict[str, float] = Field(
        default_factory=lambda: {"futures": 0.3, "option": 1.5}
    )  # 계약 명목 대비 왕복 비용(bp) — 성과·실현손익 비용 추정
    products: dict[str, FoProduct] = Field(
        default_factory=lambda: {
            "mini": FoProduct(
                futures_prefix=("A05", "105"),
                call_prefix=("B05", "205"),
                put_prefix=("C05", "305"),
                multiplier=50000,
                futures_tick=Decimal("0.02"),
                option_ticks=[("3", "0.01"), ("10", "0.02"), ("inf", "0.05")],
                name="미니코스피200",
            ),
            "regular": FoProduct(
                futures_prefix=("A01", "101"),
                call_prefix=("B01", "201"),
                put_prefix=("C01", "301"),
                multiplier=250000,
                futures_tick=Decimal("0.05"),
                option_ticks=[("10", "0.01"), ("inf", "0.05")],
                name="코스피200",
            ),
        }
    )

    @model_validator(mode="after")
    def _product_known(self):
        if self.product not in self.products:
            raise ValueError(f"fo.product {self.product!r} 가 products 에 없습니다")
        return self

    def product_cfg(self) -> FoProduct:
        return self.products[self.product]

    def regime_cap(self, regime: str) -> float:
        """국면별 배수 상한. 미설정(null) 은 max_leverage, 0 은 0.0 그대로(신규 금지).
        설정값이 max_leverage 보다 커도 max_leverage 로 자른다."""
        if regime not in self.leverage_by_regime:
            return self.max_leverage
        v = self.leverage_by_regime[regime]
        if v is None:
            return self.max_leverage
        return min(float(v), self.max_leverage)


class FoMonitorWindow(BaseModel):
    """schedule.yaml `fo.kr.monitor`: 파생 장중 감시 주기(초)와 KST 시각 창. accounts 의
    `MonitorWindow` 와 같은 필드이지만 레인은 다른 기능을 import 하지 않아 따로 둔다."""

    model_config = ConfigDict(extra="forbid")

    start: str
    end: str
    interval_s: int = Field(default=60, ge=5, le=3600)

    @field_validator("start", "end")
    @classmethod
    def _v(cls, v: str) -> str:
        return check_hm(v)


class FoMarketSchedule(BaseModel):
    """파생 레인(Phase 26) 시장별 시각: 아침 세션 cron + 모니터 창(KRX 파생 정규장 08:45~15:45)."""

    model_config = ConfigDict(extra="forbid")

    session: str = "08:20"
    monitor: FoMonitorWindow = Field(
        default_factory=lambda: FoMonitorWindow(start="08:45", end="15:45", interval_s=60)
    )

    @field_validator("session")
    @classmethod
    def _v(cls, v: str) -> str:
        return check_hm(v)


class FoSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kr: FoMarketSchedule = Field(default_factory=FoMarketSchedule)


SECTIONS = (
    Section("settings", "fo", FoCfg, FoCfg),
    Section("schedule", "fo", FoSchedule, FoSchedule),
)
