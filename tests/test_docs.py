"""문서 계약: CLAUDE.md 크기 상한과 TODO 마일스톤 체크리스트."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_claude_md_under_16kb() -> None:
    data = (ROOT / "CLAUDE.md").read_bytes()
    assert len(data) <= 16 * 1024, f"CLAUDE.md {len(data)} bytes > 16384"


def test_todo_has_milestone_checklist() -> None:
    text = (ROOT / "docs" / "TODO.md").read_text(encoding="utf-8")
    for n in range(13):
        assert re.search(rf"^#+\s*M{n}\b", text, re.M), f"M{n} 제목 없음"
    m0 = re.split(r"^#+\s*M1\b", text, maxsplit=1, flags=re.M)[0]
    m0 = m0.split("M0", 1)[1]
    assert re.search(r"^- \[[ x]\]", m0, re.M)


def _cmd_name(info) -> str:
    """typer 명령 이름(이름이 없으면 typer 규칙대로 함수 이름에서)."""
    return info.name or info.callback.__name__.lower().replace("_", "-")


def _leaves(typer_app) -> list[str]:
    """숨김이 아닌 하위 명령·그룹 이름을 재귀로 모은다."""
    out = [_cmd_name(c) for c in typer_app.registered_commands if not c.hidden]
    for g in typer_app.registered_groups:
        out.append(g.name)
        out += _leaves(g.typer_instance)
    return out


def test_claude_md_lists_every_cli_command() -> None:
    """루트 명령과 그룹(기능 포함)의 리프 명령이 CLAUDE.md 「실행 명령」 표에 있다.
    `tool` 은 LLM 전용이라 「`trader tool …`」 한 행만 둔다."""
    from trader.kernel.cli.main import build_app
    from trader.kernel.registry import discover

    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    section = text.split("## 실행 명령", 1)[1].split("\n## ", 1)[0]
    # 명령 열(첫 번째 열)만 본다 — 용도 열의 언급으로 통과하지 않게(T19). `\|` 는 열 구분이 아님
    rows = [
        re.split(r"(?<!\\)\|", line)[1]
        for line in section.splitlines()
        if line.startswith("|") and line.count("|") >= 3
    ]
    cmds = "\n".join(rows)
    root = build_app(discover())
    missing: list[str] = []
    for c in root.registered_commands:
        if not c.hidden and f"trader {_cmd_name(c)}" not in cmds:
            missing.append(_cmd_name(c))
    for g in root.registered_groups:
        mine = " ".join(r for r in rows if f"trader {g.name}" in r)
        if not mine:
            missing.append(g.name)
            continue
        if g.name == "tool":
            continue
        for leaf in _leaves(g.typer_instance):
            if not re.search(rf"(?<![\w-]){re.escape(leaf)}(?![\w-])", mine):
                missing.append(f"{g.name} {leaf}")
    assert not missing, f"CLAUDE.md 명령표에 없는 명령: {missing}"
