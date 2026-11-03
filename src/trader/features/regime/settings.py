"""regime 설정 섹션: settings.yaml 의 `hedge`·`event_blackout`,
schedule.yaml 의 `regime`·`ksd_events`.

yaml 키를 더하면 `templates/config/settings.yaml` 도 같이 바꾼다."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trader.kernel.config import Market
from trader.kernel.config.sections import Section


class HedgeInstrument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    exchange: Literal["NAS", "NYS", "AMS"] | None = None  # US 만


def _coerce_instrument(v: Any) -> Any:
    return {"symbol": v} if isinstance(v, str) else v


def hedge_tickers(cfg: HedgeCfg, market: str) -> frozenset[str]:
    """그 시장의 헤지 상품 코드. 헤지가 꺼져 있으면 빈 집합."""
    return frozenset(cfg.tickers(market)) if cfg.enabled else frozenset()


class HedgeCfg(BaseModel):
    """하락장 대응 (Phase 17 H): 국면 판정 3지표 + 인버스 ETF 헤지 슬리브. 전부 파이썬 규칙.

    국면: ① 지수 proxy 종가 < 200일 SMA, ② 20거래일 수익률 ≤ momentum_min, ③ 변동성(US 는 FRED VIX ≥
    vix_min, KR 은 proxy 20일 실현변동성 연율 ≥ realized_vol_min). count ≥ bear_min_signals → bear,
    0 → bull, 그 외 neutral. bear 해제는 count ≤ 1 이 exit_streak_days 연속일 때(채찍질 방지)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_weight_pct: float = Field(default=0.20, ge=0, le=0.5)
    min_weight_pct: float = Field(default=0.05, ge=0, le=0.5)  # 시장 세션 제안의 하한 (Phase 18)
    market_session: bool = True  # 매일 시장 세션(거시 애널리스트·토론·전략가) 실행 여부 (Phase 18)
    leverage: bool = True  # 3지표 모두 켜지면 −2x, 아니면 −1x
    bear_min_signals: int = Field(default=2, ge=1, le=3)
    exit_streak_days: int = Field(default=5, ge=1, le=30)
    # Phase 19: 전략가(시장 세션) RISK_OFF 를 4번째 국면 지표로 센다(지표 0개면 bear 진입 불가),
    # RISK_ON 은 지표가 bear_min_signals 와 같을 때 하루 진입 보류. 둘 다 confidence 기준 이상만
    agent_signal: bool = False  # 전체 검토 L8: 기울기(agent_gate)와 이중 반영이라 기본 끔
    agent_min_confidence: float = Field(default=0.7, ge=0, le=1)
    # Phase 22: 전략가 제안(확신 ≥ agent_min_confidence)이 국면 한도를 한 칸 옮긴다 —
    # neutral+RISK_ON 20%·4, neutral+RISK_OFF 10%·2, bull+RISK_OFF → neutral 한도, bear+RISK_ON →
    # 신규 매수를 10%·2 한도로 조건부 허용. 헤지·손절은 무관. 끄면 Phase 17 국면 한도 그대로
    agent_gate: bool = True
    # Phase 21: KR 외인 수급(지수 proxy 20일·5일 순매수 합 모두 음수) 을 보조 지표로 센다. 기본 끔
    flow_signal: bool = False
    # Phase 21: bear 유지 중 보유 상품(−1x/−2x)이 판정과 다른 날이 switch_streak_days 연속이면 전량
    # 매도 뒤 다음 운영일 재진입(같은 날 둘 다 내지 않음 — 매도 현금을 확인할 수 없다). 기본 끔
    switch_instruments: bool = False
    switch_streak_days: int = Field(default=3, ge=1, le=30)
    ma_days: int = Field(default=200, ge=20, le=400)
    momentum_days: int = Field(default=20, ge=5, le=120)
    momentum_min: float = -0.05
    vix_min: float = 25.0
    realized_vol_min: float = 0.25
    instruments: dict[Market, dict[Literal["inverse", "inverse2x"], HedgeInstrument]] = Field(
        default_factory=lambda: {
            "KR": {
                "inverse": HedgeInstrument(symbol="114800"),
                "inverse2x": HedgeInstrument(symbol="252670"),
            },
            "US": {
                "inverse": HedgeInstrument(symbol="SH", exchange="AMS"),
                "inverse2x": HedgeInstrument(symbol="SDS", exchange="AMS"),
            },
        }
    )
    index_proxy: dict[Market, HedgeInstrument] = Field(
        default_factory=lambda: {
            "KR": HedgeInstrument(symbol="069500"),
            "US": HedgeInstrument(symbol="SPY", exchange="AMS"),
        }
    )

    @model_validator(mode="after")
    def _weights(self) -> HedgeCfg:
        if self.min_weight_pct > self.max_weight_pct:
            raise ValueError("hedge.min_weight_pct 는 max_weight_pct 이하여야 합니다")
        return self

    @field_validator("instruments", mode="before")
    @classmethod
    def _coerce_instruments(cls, v: Any) -> Any:
        if not isinstance(v, dict):
            return v
        return {m: {k: _coerce_instrument(x) for k, x in (d or {}).items()} for m, d in v.items()}

    @field_validator("index_proxy", mode="before")
    @classmethod
    def _coerce_proxy(cls, v: Any) -> Any:
        if not isinstance(v, dict):
            return v
        return {m: _coerce_instrument(x) for m, x in v.items()}

    def tickers(self, market: str) -> set[str]:
        """그 시장의 헤지 종목 코드 (monitor·종목 수·섹터 계산·스캔에서 제외)."""
        return {i.symbol for i in self.instruments.get(market, {}).values()}  # type: ignore[arg-type]

    def instrument(self, market: str, *, leveraged: bool) -> HedgeInstrument | None:
        d = self.instruments.get(market, {})  # type: ignore[arg-type]
        if leveraged and self.leverage and "inverse2x" in d:
            return d["inverse2x"]
        return d.get("inverse")


class BlackoutCfg(BaseModel):
    """권리 일정 블랙아웃(Stock D-113, **기본 끔**): 예탁원 확정 기준일 `before` 일 전 ~ `after`
    일 뒤 신규 매수 금지(매도는 무관). 수집은 켜져 있으면 데몬이 매일 한다."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    kinds: list[Literal["paidin", "bonus", "rev_split", "merger_split"]] = Field(
        default_factory=lambda: ["paidin", "bonus", "rev_split", "merger_split"]
    )
    before_days: int = Field(default=5, ge=0, le=30)
    after_days: int = Field(default=2, ge=0, le=30)


SECTIONS = (
    Section("settings", "hedge", HedgeCfg, HedgeCfg),
    Section("settings", "event_blackout", BlackoutCfg, BlackoutCfg),
)


_HM = r"^([01]\d|2[0-3]):([0-5]\d)$"


class AtTime(BaseModel):
    """잡 시각: "HH:MM" 과 시간대(기본 KST)·요일(APScheduler 표기, 없으면 잡 기본 mon-fri)."""

    model_config = ConfigDict(extra="forbid")

    time: str = Field(pattern=_HM)
    tz: str | None = None
    days: str | None = None


class RegimeSchedule(BaseModel):
    """schedule.yaml `regime`: 시장별 국면 판정 시각(scan 앞, US 는 뉴욕 시각)."""

    model_config = ConfigDict(extra="forbid")

    kr: str | AtTime = "08:25"  # 문자열이면 KST
    us: str | AtTime = Field(default_factory=lambda: AtTime(time="09:20", tz="America/New_York"))


REGIME_SCHEDULE = Section("schedule", "regime", RegimeSchedule, RegimeSchedule)
KSD_SCHEDULE = Section("schedule", "ksd_events", str, lambda: "07:05")
