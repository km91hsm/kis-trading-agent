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
