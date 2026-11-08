"""기능 소유 연구 계약과 모델 입력 메타데이터."""

from pathlib import Path

from trader.kernel.registry.types import ResearchSpec


def metadata(directory: Path, inputs: dict) -> dict[str, str]:
    market = inputs.get("market") or directory.name
    return {
        "market": market,
        "ticker": inputs.get("proxy") or ("KOSPI200" if market == "KR" else "SPX"),
        "currency": "KRW" if market == "KR" else "USD",
        "asof": inputs.get("asof") or inputs.get("date") or "unknown",
    }


RESEARCH = ResearchSpec(
    scope="market",
    input_stage="_market_inputs",
    report_prefix="_market_analyst",
    turn_prefix="_market_debate_r",
    cross_turn_prefix="_market_debatex_r",
    completed_stage="_market_view",
    summary_stage="_market_summary",
    cli_template="market codex-turn --market <market> --date <date> --round <n>",
    bull_agent="market-researcher-bull",
    bear_agent="market-researcher-bear",
    metadata=metadata,
)
