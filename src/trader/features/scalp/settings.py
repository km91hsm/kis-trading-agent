"""scalp 설정: settings.yaml 의 `scalp`.

yaml 키를 더하면 `templates/config/*.yaml` 도 같이 바꾼다."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from trader.kernel.config.sections import Section


class ScalpCfg(BaseModel):
    """분 단위 단타 층. **계좌별 스위치(control `scalp_enabled:<id>`)** — `enabled` 는 모의 계좌
    기본값(기본 끔). 1분봉 신호는 파이썬 순수 함수이고 에이전트는 후보(picks·뉴스·watch)와 허용
    여부(strategist `scalp_ok`)만 정한다. 실전 계좌는 기본 꺼짐이고, 스위치를 켜도 계좌 모드가
    자동(LIVE 확인)이어야 주문한다."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    max_weight_pct: float = Field(default=0.05, gt=0, le=0.25)  # 계좌 NAV 대비 진입 비중
    max_positions: int = Field(default=2, ge=1, le=10)  # 동시 보유 단타 종목 수
    max_universe: int = Field(default=10, ge=1, le=30)  # 틱마다 분봉을 받는 종목 수 상한
    volume_mult: float = Field(default=3.0, gt=1)  # 최근 5분 거래량 ≥ 직전 20분 평균 × 배수
    take_profit_pct: float = Field(default=0.015, gt=0, le=0.2)
    stop_pct: float = Field(default=0.01, gt=0, le=0.2)
    max_hold_min: int = Field(default=60, ge=5, le=390)
    warmup_min: int = Field(default=15, ge=1, le=120)  # 개장 뒤 이 시간은 진입 없음
    close_before_min: int = Field(default=20, ge=1, le=120)  # 마감 전: 신규 금지·전량 청산
    daily_stop_pct: float = Field(default=0.01, gt=0, le=0.2)  # 일일 실현손실(NAV 비중 합) 한도
    # 분 단위 LLM 판정자(scalp-judge, 기본 끔): 신호 뒤 비동기 판정 → 다음 틱 회수
    judge: bool = False
    judge_daily_cap: int = Field(default=60, ge=1, le=500)  # 판정 호출 하루 상한
    judge_timeout_s: float = Field(default=45, gt=0, le=300)  # 이 시간 안에 판정이 없으면 버림
    judge_max_drift_pct: float = Field(default=0.003, gt=0, le=0.05)  # 판정 사이 신호가 대비 변동
    judge_hold_every_min: int = Field(default=5, ge=1, le=60)  # 보유 중 유지/청산 조언 주기
    # 진입·청산 알림 묶음 주기: 첫 묶음은 바로, 그 뒤는 이 간격 · 중단·마감은 즉시
    digest_every_min: int = Field(default=30, ge=1, le=390)


SECTIONS = (Section("settings", "scalp", ScalpCfg, ScalpCfg),)
