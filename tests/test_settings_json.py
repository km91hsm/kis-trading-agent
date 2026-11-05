"""`.claude/settings.json` 계약: 시크릿 읽기·사용자 전용 명령 거부, 세션 시작 훅."""

from __future__ import annotations

import json
from fnmatch import fnmatchcase
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

USER_ONLY = (
    "daemon",
    "install",
    "app",
    "accounts order-test",
    "accounts mode",
    "spot approvals approve",
    "spot monitor",
    "fo order-test",
    "fo close",
    "fo monitor",
    "fo session",
    "saveticker run",
    "saveticker service",
    "saveticker install-browser",
)


def _bash_denied(deny: list[str], cmd: str) -> bool:
    pats = [d[len("Bash(") : -1] for d in deny if d.startswith("Bash(") and d.endswith(")")]
    return any(fnmatchcase(cmd, p) for p in pats)


def test_settings_json_denies_user_only_commands_and_secrets() -> None:
    data = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    deny = data["permissions"]["deny"]
    # 시크릿: Read 도구와 Bash 읽기 명령 두 형태
    assert "Read(./secrets/.env)" in deny
    for reader in ("cat", "head", "tail", "less", "more"):
        for path in ("secrets/.env", "./secrets/.env"):
            assert _bash_denied(deny, f"{reader} {path}"), f"{reader} {path} 미거부"
    assert _bash_denied(deny, "grep KIS secrets/.env")
    # .env.example 은 키 이름 템플릿이라 막지 않는다
    for cmd in ("cat secrets/.env.example", "cat templates/secrets/.env.example"):
        assert not _bash_denied(deny, cmd), f"{cmd} 가 거부됨"
    assert not any(".env.example" in d for d in deny)
    # 사용자 전용 명령: uv run · python -m 두 형태
    for cmd in USER_ONLY:
        assert f"Bash(uv run trader {cmd}*)" in deny, cmd
        assert f"Bash(python -m trader {cmd}*)" in deny, cmd
    # 세션 시작 훅은 파이썬
    hooks = data["hooks"]["SessionStart"]
    commands = [h["command"] for entry in hooks for h in entry["hooks"]]
    assert any("uv run python" in c and "tools/session_start.py" in c for c in commands)
    assert not any(c.endswith((".sh", ".bat", ".ps1")) for c in commands)
