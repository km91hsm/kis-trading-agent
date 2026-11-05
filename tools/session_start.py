"""SessionStart 훅: `state/STATUS.md` 의 「운영 점검」·「데몬」 절과 `docs/TODO.md` 열린
항목을 출력한다.

`.claude/settings.json` 이 세션 시작 때 `uv run --no-project python tools/session_start.py`
로 부른다(프로젝트 동기화 없이). 표준 라이브러리만 쓰고 macOS·Windows 공용이다. 출력은
UTF-8, 전체 10 000자 상한. STATUS.md 는 `trader status` 가 만든다(절 제목은
`kernel/ops/status.py::KERNEL_STATUS_SECTIONS`).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

MAX_CHARS = 10_000
MAX_TODOS = 10
SECTIONS = ("운영 점검", "데몬")
_OPEN = re.compile(r"^\s*- \[ \]\s*(.*)$")
_CUT = "\n…(10 000자 상한으로 잘림)"


def _root() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env) if env else Path(__file__).resolve().parents[1]


def _read(path: Path) -> str | None:
    try:
        # 깨진 바이트가 섞여도 훅이 죽지 않게 치환한다(UnicodeDecodeError 방지)
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _sections(text: str, titles: tuple[str, ...]) -> list[str]:
    """`## 제목` 절 중 titles 에 든 것만 원문 그대로(제목 줄 포함)."""
    blocks: list[list[str]] = []
    keep = False
    for line in text.splitlines():
        if line.startswith("## "):
            keep = line[3:].strip() in titles
            if keep:
                blocks.append([line])
        elif keep:
            blocks[-1].append(line)
    return ["\n".join(b).rstrip() for b in blocks]


def _open_todos(text: str, limit: int) -> list[str]:
    items = []
    for line in text.splitlines():
        m = _OPEN.match(line)
        if m:
            items.append(f"- [ ] {m.group(1).strip()}")
            if len(items) >= limit:
                break
    return items


def main(root: Path | None = None) -> str:
    """세션 컨텍스트 문자열(≤ MAX_CHARS). STATUS 절을 먼저 자르고 TODO 는 지킨다."""
    root = root or _root()
    status = _read(root / "state" / "STATUS.md")
    if status is None:
        head = "# 시스템 상태\n\n`state/STATUS.md` 가 없습니다. `uv run trader status` 로 만드세요."
    else:
        blocks = _sections(status, SECTIONS)
        head = "# 시스템 상태 (state/STATUS.md 발췌)\n\n" + (
            "\n\n".join(blocks) if blocks else "(운영 점검·데몬 절 없음)"
        )
    todos = _open_todos(_read(root / "docs" / "TODO.md") or "", MAX_TODOS)
    tail = f"\n\n## 열린 TODO (앞 {len(todos)}개)\n" + "\n".join(todos) if todos else ""
    room = MAX_CHARS - len(tail)
    if len(head) > room:
        head = head[: room - len(_CUT)].rstrip() + _CUT
    return (head + tail)[:MAX_CHARS]


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(main())
