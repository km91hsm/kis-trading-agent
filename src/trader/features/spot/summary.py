"""리서치 토론 수렴 감시와 요약 병합 — 정본은 `trader.kernel.session.debate`(재export)."""

from trader.kernel.session.debate import (
    _DIRECTION,
    _SIGN,
    CONSENSUS_ROLES,
    CONVERGENCE_GAP,
    analyst_consensus,
    conviction_gap,
    research_summary,
    signal_direction,
    signed_conviction,
    skipped_summary,
)

__all__ = [
    "CONSENSUS_ROLES",
    "CONVERGENCE_GAP",
    "_DIRECTION",
    "_SIGN",
    "analyst_consensus",
    "conviction_gap",
    "research_summary",
    "signal_direction",
    "signed_conviction",
    "skipped_summary",
]
