"""기능 소유 연구 계약과 모델 입력 메타데이터."""

from pathlib import Path

from trader.kernel.registry.types import ResearchSpec


def metadata(directory: Path, inputs: dict) -> dict[str, str]:
    meta = inputs.get("meta") or {}
    return {
        "market": "KR",
        "ticker": meta.get("proxy") or "KOSPI200",
        "currency": "KRW",
        "asof": meta.get("asof") or meta.get("date") or "unknown",
    }


RESEARCH = ResearchSpec(
    scope="fo",
    input_stage="_fo_inputs",
    report_prefix="_fo_analyst_",
    turn_prefix="_fo_debate_r",
    completed_stage="_fo_decision",
    summary_stage="_fo_summary",
    cli_template="fo codex-turn --market KR --date <date> --round <n>",
    bull_agent="researcher-bull",
    bear_agent="researcher-bear",
    metadata=metadata,
)
