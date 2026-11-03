"""evaluation 공개면. 다른 기능은 이 모듈만 import 한다."""

from trader.features.evaluation import (
    daily_report,
    lots,
    open_window,
    paper_review,
    performance,
    readiness,
    shadow,
)
from trader.features.evaluation.memory.outcomes import SIGN, close_table, window_return
from trader.features.evaluation.memory.outcomes import mature as mature_memory
from trader.features.evaluation.memory.store import (
    fill_outcome,
    get_entry,
    matured_count,
    record_decision,
    retrieve,
)
from trader.features.evaluation.memory.vector import DIMS, cosine, situation_vector
from trader.features.evaluation.metrics import convergence_rate, decision_distribution
from trader.features.evaluation.performance import benchmarks, metrics_of, nav_series
from trader.features.evaluation.readiness import MIN_SHADOW_PAIRS, interval_kinds_of
from trader.features.evaluation.rl import report as rl_report
from trader.features.evaluation.rl import shadow as rl_shadow
from trader.features.evaluation.settings import MemorySettings

lots_report = lots.report
rl_comparison = rl_report.comparison
rl_current_model = rl_report.current_model
rl_recent = rl_shadow.recent
shadow_rows = shadow.rows
shadow_mature = shadow.mature
index_table = shadow.index_table
paired_stats = shadow.paired_stats
cash_effect = shadow.cash_effect
open_window_targets = open_window.targets
performance_report = performance.report
readiness_check = readiness.check

__all__ = [
    "MIN_SHADOW_PAIRS",
    "DIMS",
    "MemorySettings",
    "SIGN",
    "close_table",
    "benchmarks",
    "cash_effect",
    "convergence_rate",
    "cosine",
    "daily_report",
    "decision_distribution",
    "fill_outcome",
    "get_entry",
    "index_table",
    "interval_kinds_of",
    "lots_report",
    "mature_memory",
    "open_window_targets",
    "paper_review",
    "paired_stats",
    "performance_report",
    "matured_count",
    "metrics_of",
    "nav_series",
    "record_decision",
    "readiness_check",
    "retrieve",
    "rl_comparison",
    "rl_current_model",
    "rl_recent",
    "shadow_mature",
    "shadow_rows",
    "situation_vector",
    "window_return",
]
