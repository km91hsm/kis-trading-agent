"""ml 설정 섹션: ml.yaml 의 `label`·`train`·`models`,
schedule.yaml 의 `ml_train`·`universe_members`.

yaml 키를 더하면 `templates/config/ml.yaml` 도 같이 바꾼다."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from trader.kernel.cli.boot import build_registry
from trader.kernel.config.loader import memo_load
from trader.kernel.config.sections import Section, SectionRegistry
from trader.kernel.registry import discover


class LabelThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    up: float = 0.02
    down: float = -0.02


class LabelCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    horizon_days: int = Field(default=5, ge=1, le=60)
    thresholds: LabelThresholds = Field(default_factory=LabelThresholds)
    embargo_days: int = Field(default=5, ge=0, le=60)
    # absolute: fwd_return 을 ±thresholds 로 3분류. relative_tercile: 같은 날·시장 안 순위 삼분위
    # (기준선 1/3 고정, 시장 등락이 라벨에서 빠짐 — scan 랭커 용도와 맞는다)
    mode: Literal["absolute", "relative_tercile"] = "absolute"


class PitCfg(BaseModel):
    """시점 기준 유니버스(Stock D-037): 종목이 그날 유니버스에 있었을 때의 행만 학습한다.
    - 기록(`universe_members`) 시작일 이후는 그날 구성원만(편출·상장폐지 종목의 과거 행은 남는다)
    - strict: 기록 전 날짜(현재 구성으로 백필한 과거)를 아예 뺀다 — 기록이 충분히 쌓인 뒤 켠다
    - liquidity_top_n: 그날까지의 `liquidity_window` 일 평균 거래대금 상위 N 만(0 이면 끔) —
      '지금 유동성 높은 종목' 이 과거에도 그랬다고 보는 선택 편향을 줄인다"""

    model_config = ConfigDict(extra="forbid")

    strict: bool = False
    liquidity_top_n: int = Field(default=0, ge=0, le=2000)
    liquidity_window: int = Field(default=20, ge=5, le=120)


class TrainCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_years: int = Field(default=3, ge=1, le=10)
    walk_forward_folds: int = Field(default=6, ge=2, le=20)
    pit: PitCfg = Field(default_factory=PitCfg)


class QuantModelCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    promote_min_accuracy: float = 0.40
    promote_min_alpha: float = 0.0
    # accuracy: 합산 정확도 > 기준선 ∧ ≥ promote_min_accuracy ∧ 알파 > promote_min_alpha (기존).
    # alpha: scan 랭커 용도 — 평균 알파(상위 N 초과수익) ≥ promote_min_alpha ∧ 알파가 양인 폴드
    #        비율 ≥ promote_min_positive_folds. 정확도는 보지 않는다 (상대 라벨 기준선은 1/3 고정).
    promote_metric: Literal["accuracy", "alpha"] = "accuracy"
    promote_min_positive_folds: float = Field(default=0.67, ge=0.0, le=1.0)
    promote_top_n: int = Field(default=5, ge=1, le=50)  # alpha_topn 의 N (scan 랭커 상위 N 과 같게)
    backend: Literal["auto", "sklearn", "lightgbm"] = "sklearn"  # 플랫폼 간 동일 동작을 위해 고정
    min_up_predictions: int = 5  # 폴드마다 up 예측 최소 건수 (알파 추정 노이즈 방지)
    max_iter: int = Field(default=200, ge=1, le=5000)  # 부스팅 반복(트리 수). 테스트는 작게
    # `ml tune` 이 탐색하는 두 값 (sklearn max_leaf_nodes / lightgbm num_leaves). 자동 적용 없음.
    learning_rate: float = Field(default=0.05, gt=0, le=1.0)
    num_leaves: int = Field(default=15, ge=2, le=255)
    # 알파 기준은 왕복비용 기준이라 시장마다 다를 수 있다 (KR 30bp, US 50bp).
    # 없는 시장은 promote_min_alpha.
    promote_min_alpha_by_market: dict[Literal["KR", "US"], float] = Field(default_factory=dict)

    def min_alpha_for(self, market: str | None) -> float:
        if market is not None and market in self.promote_min_alpha_by_market:
            return self.promote_min_alpha_by_market[market]
        return self.promote_min_alpha


class RankerCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True


class CalibrationCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    min_decisions: int = 60
    # 트레이더 입력(35_calibration)에 넣을지. 기본 false — 현 보정은 역할·이웃 표본 수와 무관하게
    # 할인을 지시해(이웃 k=20 < n≥30) L12 개선 전에는 넣지 않는다. 만기 min_decisions 도달은 운영
    # 점검의 재검토 알림이다(2026-10-28 결정 14번)
    inject: bool = False


class RlSizerCfg(BaseModel):
    """RL 사이저 (스펙 §9.4, 실험). 모의 계좌 한정, 승격은 `trader rl promote` 수동."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    seeds: int = Field(default=3, ge=1, le=10)
    mode: Literal["shadow", "apply"] = "shadow"  # apply 도 모의 계좌에만
    backend: Literal["auto", "cem", "ppo"] = "auto"  # ppo 는 `uv sync --extra rl`
    max_weight: float = Field(default=0.25, gt=0, le=0.25)
    drawdown_penalty: float = Field(default=1.0, ge=0)
    cost_bp: int = Field(default=30, ge=0)
    total_timesteps: int = Field(default=20000, ge=1)
    cem_iterations: int = Field(default=30, ge=1)
    cem_population: int = Field(default=64, ge=4)
    min_memory_steps: int = Field(default=200, ge=1)
    min_steps: int = Field(default=300, ge=1)
    fixed_weight: float = Field(default=0.10, ge=0, le=0.25)
    horizon_days: int = Field(default=5, ge=1, le=60)  # 보상 정규화용, train 이 label 값으로 덮음


class MlModelsCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quant: QuantModelCfg = Field(default_factory=QuantModelCfg)
    ranker: RankerCfg = Field(default_factory=RankerCfg)
    calibration: CalibrationCfg = Field(default_factory=CalibrationCfg)
    rl_sizer: RlSizerCfg = Field(default_factory=RlSizerCfg)


class MlTrainSchedule(BaseModel):
    """schedule.yaml `ml_train`: 주간 재학습 시각(기본 토요일 10:00 KST)."""

    model_config = ConfigDict(extra="forbid")

    time: str = "10:00"
    days: str = "sat"
    tz: str | None = None


SECTIONS = (
    Section("ml", "label", LabelCfg, LabelCfg),
    Section("ml", "train", TrainCfg, TrainCfg),
    Section("ml", "models", MlModelsCfg, MlModelsCfg),
)
SCHEDULE_SECTIONS = (
    Section("schedule", "ml_train", MlTrainSchedule, MlTrainSchedule),
    Section("schedule", "universe_members", str, lambda: "06:40"),
)


def load_ml(registry: SectionRegistry | None = None, path: Path | None = None) -> Any:
    """`ml` 파일의 3섹션 모델(`label`·`train`·`models`). 파일이 없으면 기본값."""
    return memo_load("ml", registry or build_registry(discover()), path)


def ml_config(data: dict | None = None) -> Any:
    """기본값·테스트용: 등록된 `ml` 파일 모델을 dict 로 직접 만든다."""
    return build_registry(discover()).model_for("ml").model_validate(data or {})
