"""토론 턴의 근거 ID 안내 — 묶음 입력·거부 메시지·서브에이전트 프롬프트가 같은 규칙을 말한다."""

from pathlib import Path

import pytest

from trader.kernel.session import tooling

ROOT = Path(__file__).resolve().parents[3]
RESEARCHER_PROMPTS = [
    "src/trader/features/spot/agents/researcher-bull.md",
    "src/trader/features/spot/agents/researcher-bear.md",
    "src/trader/features/regime/agents/market-researcher-bull.md",
    "src/trader/features/regime/agents/market-researcher-bear.md",
]
ORCHESTRATOR_PROMPTS = [
    "src/trader/features/spot/agents/orchestrator.md",
    "src/trader/features/regime/agents/market-orchestrator.md",
    "src/trader/features/fo/agents/fo-orchestrator.md",
]


def _report(*ids):
    return {"evidence": [{"claim": "c", "source_id": i} for i in ids]}


def test_report_source_ids_are_unique_sorted_and_skip_malformed_entries():
    reports = [_report("ratios", "history"), _report("ratios"), {"evidence": ["문자열"]}, {}]
    assert tooling.report_source_ids(reports) == ["history", "ratios"]


def test_debate_bundle_lists_allowed_evidence_from_reports():
    bundle = {"round": 0, "side": "bull", "reports": [_report("technical.daily", "ratios")]}
    result = tooling.debate_bundle(bundle, 24_000)
    assert result["ok"] is True
    assert result["data"]["allowed_evidence"] == ["ratios", "technical.daily"]


@pytest.mark.parametrize("path", RESEARCHER_PROMPTS)
def test_researcher_prompt_requires_exact_allowed_evidence(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    assert "`evidence` 배열에는 입력 `allowed_evidence` 의 값만" in text


@pytest.mark.parametrize("path", RESEARCHER_PROMPTS)
def test_researcher_prompt_keeps_data_gap_note_within_argument_cap(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    assert "`arguments` 끝에" not in text
    assert "5개 안에" in text


@pytest.mark.parametrize("path", ORCHESTRATOR_PROMPTS)
def test_orchestrator_forwards_rejection_reason_on_researcher_retry(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    assert "토론 턴 저장이 거부되면 도구의 거부 이유 전문을" in text
