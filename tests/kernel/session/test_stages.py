from trader.kernel.registry.types import StageRule
from trader.kernel.session import run_store, stages


def test_check_rule_pattern_requires_min_counts():
    rule = StageRule("bull_*", requires=("analyst",), min_counts=(("bull_*", 2),))
    assert stages.check_rule(rule, "bull_1", ["analyst", "bull_1", "bull_2"]) == []
    assert stages.check_rule(rule, "bear_1", []) == ["단계 bear_1 는 이 도구가 쓸 수 없음"]
    assert stages.check_rule(rule, "bull_1", ["bull_1"]) == [
        "analyst 없음",
        "bull_* 2개 필요(현재 1)",
    ]


def test_first_rule_picks_matching_pattern():
    a, b = StageRule("a_*"), StageRule("b_*")
    assert stages.first_rule((a, b), "b_1") is b
    assert stages.first_rule((a, b), "c") is None


def test_run_store_reexports_schema_version():
    assert run_store.CHECKPOINT_SCHEMA_VERSION is stages.CHECKPOINT_SCHEMA_VERSION == 1
