"""`/status`·STATUS.md 의 「ML 모델」 절: quant 모델 최신 3개."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from trader.features.ml import registry
from trader.kernel.config import KST
from trader.kernel.registry.types import StatusSection

RECENT = 3


def _kst_date(iso: str | None) -> str:
    try:
        return datetime.fromisoformat(str(iso)).astimezone(KST).date().isoformat()
    except (TypeError, ValueError):
        return "-"


def model_lines(ctx: Any) -> list[str]:
    """'- 20261101_153000 KR 정확도 0.61 · 승격 (2026-11-01)'. 정확도는 walk-forward 지표
    (`metrics.accuracy` — 메타에 AUC 는 없다), 날짜는 저장 시각의 KST 날짜."""
    metas = registry.list_models("quant", ctx.state)[:RECENT]
    if not metas:
        return ["- ML 모델 없음"]
    return [
        f"- {m.get('version')} {m.get('market') or '전체'} "
        f"정확도 {(m.get('metrics') or {}).get('accuracy', '-')} · "
        f"{'승격' if m.get('promoted') else '미승격'} ({_kst_date(m.get('saved_at'))})"
        for m in metas
    ]


STATUS_SECTION = StatusSection("ML 모델", model_lines)
