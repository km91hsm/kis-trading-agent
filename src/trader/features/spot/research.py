"""기능 소유 연구 계약과 모델 입력 메타데이터."""

from pathlib import Path

from trader.kernel.registry.types import ResearchSpec


def metadata(directory: Path, inputs: dict) -> dict[str, str]:
    meta = inputs.get("meta") or {}
    market = meta.get("market") or directory.parent.name
    return {
        "market": market,
        "ticker": meta.get("ticker") or directory.name,
        "currency": "KRW" if market == "KR" else "USD",
        "asof": meta.get("asof") or "unknown",
    }


RESEARCH = ResearchSpec(
    scope="spot",
    input_stage="00_inputs",
    report_prefix="10_analyst_",
    turn_prefix="20_debate_r",
    cross_turn_prefix="20_debatex_r",
    completed_stage="30_decision",
    summary_stage="25_research_summary",
    cli_template=(
        "checkpoint codex-turn --ticker <ticker> --market <market> --date <date> --round <n>"
    ),
    bull_agent="researcher-bull",
    bear_agent="researcher-bear",
    metadata=metadata,
)
