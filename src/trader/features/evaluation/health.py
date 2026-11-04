"""evaluation 운영 점검 영역: 보정 재검토 시점(결정 14번) — 만기 결정이 `ml.yaml
calibration.min_decisions` 에 닿았는데 주입(`inject`)이 꺼져 있으면 watch."""

from __future__ import annotations

import logging

from trader.features.evaluation.memory.store import matured_count
from trader.features.ml import api as ml_api
from trader.kernel.ops.types import Item, OpsContext
from trader.kernel.registry.types import HealthArea

logger = logging.getLogger("trader.evaluation")


def check(ctx: OpsContext) -> list[Item]:
    try:
        cal = ml_api.load_ml().models.calibration
        n = matured_count(ctx.conn)
    except Exception:  # noqa: BLE001 - 점검 보조: 설정·원장을 못 읽으면 알릴 것이 없다
        logger.debug("보정 재검토 점검 실패", exc_info=True)
        return []
    if not (cal.enabled and not cal.inject and n >= cal.min_decisions):
        return []
    return [
        Item(
            "evaluation.calibration_review",
            "watch",
            f"만기 결정 {n}건 — 보정 재검토 시점. L12 개선(벤치마크 대비 부호·Wilson 하한·"
            "수축, 역할·이웃 표본 수 조건) 뒤 `ml.yaml calibration.inject` 를 켠다 "
            "(`trader evaluation calibration build` 로 리포트 확인)",
            link="/performance",
            count=n,
        )
    ]


AREA = HealthArea("evaluation", check)
