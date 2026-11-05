"""세션 시작 훅 `tools/session_start.py`: STATUS 요약 + 열린 TODO, 10 000자 상한."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _hook():
    spec = importlib.util.spec_from_file_location(
        "session_start", ROOT / "tools" / "session_start.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


STATUS = """# 시스템 상태 (생성: 2026-11-05 09:00 KST)

## 운영 점검
- 점검 주의 · 1건 · 08:30
- orders.unknown: 확인 필요 주문 1건

## 데몬
- 실행 중 (pid 123)

## 마지막 실행
- kr_scan ok

## 구현 진행도
- M12: 1/2
"""

TODO = """# TODO

## M12
- [x] 끝난 일
- [ ] 열린 일 하나
- [ ] 열린 일 둘
"""


def test_hook_prints_status_and_todos(tmp_path: Path) -> None:
    _write(tmp_path / "state" / "STATUS.md", STATUS)
    _write(tmp_path / "docs" / "TODO.md", TODO)
    out = _hook().main(root=tmp_path)
    assert "## 운영 점검" in out and "orders.unknown" in out
    assert "## 데몬" in out and "pid 123" in out
    assert "kr_scan" not in out  # 「마지막 실행」 절은 싣지 않는다
    assert "열린 일 하나" in out and "열린 일 둘" in out
    assert "끝난 일" not in out


def test_hook_without_status_file_explains(tmp_path: Path) -> None:
    _write(tmp_path / "docs" / "TODO.md", TODO)
    out = _hook().main(root=tmp_path)
    assert "`uv run trader status` 로 만드세요" in out
    assert "열린 일 하나" in out


def test_hook_truncates_at_10000(tmp_path: Path) -> None:
    long_ops = "\n".join(f"- 항목 {i} " + "가" * 80 for i in range(400))
    status = f"# 상태\n\n## 운영 점검\n{long_ops}\n\n## 데몬\n- 꺼짐\n"
    _write(tmp_path / "state" / "STATUS.md", status)
    todo = "# TODO\n\n## M\n" + "\n".join(f"- [ ] 할 일 {i}" for i in range(50)) + "\n"
    _write(tmp_path / "docs" / "TODO.md", todo)
    out = _hook().main(root=tmp_path)
    assert len(out) <= 10_000
    assert "할 일 9" in out and "할 일 10" not in out  # 열린 항목은 최대 10개
