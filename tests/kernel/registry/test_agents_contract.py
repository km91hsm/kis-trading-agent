"""등록된 기능의 에이전트 계약: 공통 규칙 일치·스키마 이름 유일·경로 존재."""

from trader.kernel.registry.discover import discover
from trader.kernel.session import agents as ag


def _all_defs():
    return [d for f in discover() for d in f.agents]


def test_common_rules_prefix_identical_across_registered_agents():
    canon = None
    for d in _all_defs():
        rules = ag.common_rules(ag.load_agent(d).prompt)
        assert len(rules) == 6, d.name
        canon = canon or rules
        assert rules == canon, d.name


def test_agent_schema_names_unique_and_paths_exist():
    defs = _all_defs()
    ag.by_name(defs)  # 이름 중복이면 ConfigError
    for d in defs:
        assert d.path.is_file(), d.name
