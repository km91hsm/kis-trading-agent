"""spot 설정 섹션: settings.yaml 의 `risk`·`debate_rounds`·`debate`·`fill_notice`·`cash_park`.

yaml 키를 더하면 `templates/config/settings.yaml` 도 같이 바꾼다."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trader.kernel.config.sections import Section
from trader.kernel.config.time import HM_PATTERN, check_hm


class HorizonProfile(BaseModel):
    """보유기간 프로필: 결정의 `horizon_days` 가 정하는 장중 점검 주기·임계.
    단기 결정은 자주·작은 변동에, 장기 결정은 큰 변동·뉴스 묶음에만 다시 본다."""

    model_config = ConfigDict(extra="forbid")

    move_pct: float = Field(gt=0, le=0.5)  # 급등락 재평가 트리거(평단 대비)
    reeval_interval_min: int = Field(ge=1)  # 같은 종목 재평가 세션 최소 간격(분)
    periodic_check_min: int = Field(ge=0)  # 시간 단위 정기 재평가 간격(분), 0 = 없음
    news_min_novel: int = Field(ge=1)  # 뉴스 재분석에 필요한 새 뉴스 건수
    daily_reanalyze_every: int = Field(ge=1)  # 보유 종목 아침 재분석 주기(운영일)


def _default_profiles() -> dict[str, HorizonProfile]:
    return {
        "short": HorizonProfile(
            move_pct=0.04,
            reeval_interval_min=15,
            periodic_check_min=60,
            news_min_novel=1,
            daily_reanalyze_every=1,
        ),
        "mid": HorizonProfile(
            move_pct=0.07,
            reeval_interval_min=30,
            periodic_check_min=0,
            news_min_novel=1,
            daily_reanalyze_every=2,
        ),
        "long": HorizonProfile(
            move_pct=0.10,
            reeval_interval_min=60,
            periodic_check_min=0,
            news_min_novel=3,
            daily_reanalyze_every=5,
        ),
    }


class RepriceCfg(BaseModel):
    """아침 미체결 재호가: 개장 뒤 after_min 분에 운영일 1회, 오늘 낸 지정가 미체결 주문을
    취소하고 같은 결정으로 현재가 지정가 재제출(게이트 재통과). 원 지정가 대비 max_chase_pct
    넘게 불리하면 재제출하지 않고 알린다. dry_run·off 계좌는 제외."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    after_min: int = Field(default=30, ge=1, le=300)
    max_chase_pct: float = Field(default=0.01, gt=0, le=0.1)


class RiskLimits(BaseModel):
    """리스크 한도(공격적 기본값). 파이썬 리스크 규칙이 강제한다."""

    model_config = ConfigDict(extra="forbid")

    max_position_pct: float = 0.25
    max_positions: int = 5
    daily_loss_stop_pct: float = -0.05
    stop_loss_pct: float = -0.15
    min_cash_pct: float = 0.0
    max_sector_pct: float = Field(default=0.40, gt=0, le=1)  # 같은 섹터 합계 상한 (BUY/ADD 뒤)
    # 1회 손실 한도: 손절까지 잃는 금액이 NAV 의 이 비율을 넘지 않게 BUY/ADD 수량을 자른다
    risk_per_trade_pct: float = Field(default=0.02, gt=0, le=0.25)
    # 보유기간 프로필: short(≤10일)·mid(11~40)·long(>40) 셋이 전부 있어야 한다
    horizon_profiles: dict[str, HorizonProfile] = Field(default_factory=_default_profiles)
    reprice: RepriceCfg = Field(default_factory=RepriceCfg)  # 아침 미체결 재호가
    # 테마 매매 안전장치: 같은 테마 합계 상한, 당일 급등 추격 금지, 테마 종목은 짧은 보유·익절가
    max_theme_pct: float = Field(default=0.20, gt=0, le=1)
    chase_limit_pct: float = Field(default=0.10, gt=0)  # 당일 등락률 ≥ 이 값이면 신규 매수 금지
    theme_max_horizon_days: int = Field(default=10, ge=1)
    # 누적 손실 킬스위치: (계좌·시장) NAV 가 시작(또는 해제 시점) 대비 이 비율 넘게 빠지면
    # 신규 매수 중단, 해제는 사람만. 0 이면 끔
    killswitch_drawdown_pct: float = Field(default=0.15, ge=0, lt=1)

    @field_validator("horizon_profiles")
    @classmethod
    def _profiles_complete(cls, v: dict[str, HorizonProfile]) -> dict[str, HorizonProfile]:
        missing = {"short", "mid", "long"} - set(v)
        if missing:
            raise ValueError(f"horizon_profiles 에 빠진 프로필: {sorted(missing)}")
        return v


class DebateCfg(BaseModel):
    """애널리스트 합의 시 토론 생략(세션 비용).

    10_analyst_* 네 역할이 모두 같은 방향(NEUTRAL·UNKNOWN 제외)이고 확신이 전부
    `consensus_min_conviction` 이상이면 요약 단계가 토론 없이 요약을 만든다."""

    model_config = ConfigDict(extra="forbid")

    skip_on_consensus: bool = True
    consensus_min_conviction: float = Field(default=0.7, ge=0, le=1)


class FillNoticeCfg(BaseModel):
    """KIS 체결통보 웹소켓 리스너(데몬 잡 `notice_listener`). 켜면 env 별 리스너가 통보마다
    `fills_sync` 를 큐에 넣는다. 실전은 실전 계좌 모드가 off 가 아닐 때만 붙는다."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False


class CashParkCfg(BaseModel):
    """노는 현금 단기금리 ETF 운용(Stock D-115, **기본 끔**): 하락 국면이라 신규 매수가 막힌 동안만
    남는 현금(`reserve_pct` 는 남김)을 `ticker` 로 사고, 국면이 풀리면 판다. 국내만."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    ticker: str = "357870"  # TIGER CD금리투자KIS(합성)
    # NAV 대비 현금으로 남길 몫(조건부 매수·헤지)
    reserve_pct: float = Field(default=0.20, ge=0, le=1)
    min_amount: Decimal = Decimal("500000")  # 이보다 적으면 사지 않는다(수수료)


class RebalanceCfg(BaseModel):
    """주간 리밸런스(Phase 25): 최근 LLM 결정의 `target_weight` 대비 ±`band_pct` 넘게 벗어난 보유만
    되돌린다. 결정은 `lookback_days` 안의 것만 목표로 쓴다."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    band_pct: float = 0.05
    lookback_days: int = 30


SECTIONS = (
    Section("settings", "risk", RiskLimits, RiskLimits),
    Section("settings", "debate_rounds", Annotated[int, Field(ge=0, le=2)], lambda: 1),
    Section("settings", "debate", DebateCfg, DebateCfg),
    Section("settings", "fill_notice", FillNoticeCfg, FillNoticeCfg),
    Section("settings", "cash_park", CashParkCfg, CashParkCfg),
    Section("settings", "rebalance", RebalanceCfg, RebalanceCfg),
)


class FillsSyncSchedule(BaseModel):
    """schedule.yaml `fills_sync`: 체결 동기화 시각(KST "HH:MM", 월~토). KR 마감 뒤·US 마감 뒤."""

    model_config = ConfigDict(extra="forbid")

    times: list[str] = Field(default_factory=lambda: ["16:10", "07:10"], min_length=1)

    @field_validator("times")
    @classmethod
    def _hhmm(cls, v: list[str]) -> list[str]:
        for t in v:
            check_hm(t)
        return v


_HM = HM_PATTERN.pattern


class ReconcileAt(BaseModel):
    """대사 시각: "HH:MM" 과 시간대(기본 KST)."""

    model_config = ConfigDict(extra="forbid")

    time: str = Field(pattern=_HM)
    tz: str | None = None
    days: str | None = None  # APScheduler 요일 표기(없으면 잡 기본)


class ReconcileSchedule(BaseModel):
    """schedule.yaml `reconcile`: 시장별 야간 대사 시각. 마감 뒤(US 는 뉴욕 시각)."""

    model_config = ConfigDict(extra="forbid")

    kr: str | ReconcileAt = "15:45"  # 문자열이면 KST
    us: str | ReconcileAt = Field(
        default_factory=lambda: ReconcileAt(time="16:15", tz="America/New_York")
    )


class RebalanceSchedule(BaseModel):
    """schedule.yaml `rebalance`: 시장별 주간 리밸런스 시각(금요일, US 는 뉴욕 시각)."""

    model_config = ConfigDict(extra="forbid")

    kr: str | ReconcileAt = Field(default_factory=lambda: ReconcileAt(time="14:30", days="fri"))
    us: str | ReconcileAt = Field(
        default_factory=lambda: ReconcileAt(time="15:00", tz="America/New_York", days="fri")
    )


class ScanAnalyzeSchedule(BaseModel):
    """schedule.yaml `scan_analyze`: 시장별 아침 스캔 → 분석 시각(평일, US 는 뉴욕 시각).
    국면(`regime`)이 5분 앞서 돈다."""

    model_config = ConfigDict(extra="forbid")

    kr: str | ReconcileAt = "07:30"  # 문자열이면 KST
    us: str | ReconcileAt = Field(
        default_factory=lambda: ReconcileAt(time="08:30", tz="America/New_York")
    )


class BalanceRefreshSchedule(BaseModel):
    """schedule.yaml `balance_refresh`: 잔고 갱신 틱 주기(초)."""

    model_config = ConfigDict(extra="forbid")

    interval_s: int = Field(default=300, ge=1)


SCHEDULE_SECTIONS = (
    Section("schedule", "fills_sync", FillsSyncSchedule, FillsSyncSchedule),
    Section("schedule", "reconcile", ReconcileSchedule, ReconcileSchedule),
    Section("schedule", "rebalance", RebalanceSchedule, RebalanceSchedule),
    Section("schedule", "scan_analyze", ScanAnalyzeSchedule, ScanAnalyzeSchedule),
    Section("schedule", "balance_refresh", BalanceRefreshSchedule, BalanceRefreshSchedule),
)
