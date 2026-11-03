"""스크리너 랭킹 (스펙 §9.2): 승격된 퀀트 모델의 P(up) 으로 scan 후보를 재정렬한다.

필터(거래대금·변동성)는 정량 규칙 그대로 두고 점수만 z(P(up))·0.6 + z(정량)·0.4 로 바꾼다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from trader.features.ml import api as ml_api
from trader.features.scan import quant as scan_quant

logger = logging.getLogger("trader.scan")
ML_WEIGHT = 0.6
_z = ml_api.zscore


def current(
    state: Path | None = None, *, market: str | None = None, registry: Any = None
) -> tuple[Callable[[pd.DataFrame], pd.DataFrame], str] | None:
    """승격 모델이 있고 ml.yaml 에서 켜져 있으면 (rank_fn, "quant@버전"), 아니면 None.
    market 을 주면 그 시장(또는 전시장) 모델만 쓴다. registry 는 설정 레지스트리(없으면 기본)."""
    try:
        cfg = ml_api.load_ml(registry)
        if not (cfg.models.quant.enabled and cfg.models.ranker.enabled):
            return None
        found = ml_api.latest_model("quant", state=state, market=market)
    except Exception as e:  # noqa: BLE001 - ML 은 선택 입력: 어떤 오류도 scan 을 막지 않는다
        logger.warning("ML 랭커 비활성(오류): %s", e)
        return None
    if found is None:
        return None
    model, meta = found
    cols = list(meta.get("features") or model.features)
    label = f"{meta.get('name', 'quant')}@{meta.get('version', '?')}"

    def rank_fn(df: pd.DataFrame) -> pd.DataFrame:
        base = scan_quant.score(df, top=len(df) if len(df) else 1)
        if base.empty:
            return base
        rows = df.set_index("ticker").loc[base["ticker"]]
        p_up = model.predict_proba(ml_api.align(rows.reset_index(), cols))[:, 2]
        base = base.copy()
        base["p_up"] = np.round(p_up, 4)
        base["score"] = (_z(base["p_up"]) * ML_WEIGHT + _z(base["score"]) * (1 - ML_WEIGHT)).round(
            4
        )
        base["reasons"] = [
            [*r, f"ML P(up)={p:.2f}"] for r, p in zip(base["reasons"], base["p_up"], strict=True)
        ]
        out = base.sort_values("score", ascending=False).head(scan_quant.TOP_N)
        return out.reset_index(drop=True)

    return rank_fn, label
