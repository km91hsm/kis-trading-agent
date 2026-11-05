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
    "saveticker monitor",
    "saveticker install-browser",
    "accounts ping",
    "spot exec-key release",
    "regime killswitch on",
    "regime killswitch off",
    "ml promote",
    "ml demote",
    "evaluation rl promote",
    "evaluation rl demote",
)


def _bash_denied(deny: list[str], cmd: str) -> bool:
    pats = [d[len("Bash(") : -1] for d in deny if d.startswith("Bash(") and d.endswith(")")]
    return any(fnmatchcase(cmd, p) for p in pats)


def _read_denied(deny: list[str], path: str) -> bool:
    """Read 도구 규칙(gitignore 식 — `**` 는 경로 구분자도 넘는다)을 fnmatch 로 근사한다."""
    pats = [d[len("Read(") : -1] for d in deny if d.startswith("Read(") and d.endswith(")")]
    rel = path if path.startswith("./") else f"./{path}"
    return any(fnmatchcase(rel, p) for p in pats)


ALLOWED = (
    "uv run trader status",
    "uv run pytest -q",
    "uv run trader selfcheck",
    "uv run trader regime killswitch show",
    "uv run trader ml models",
)


def test_settings_json_denies_user_only_commands_and_secrets() -> None:
    data = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    deny = data["permissions"]["deny"]
    # 시크릿: Read 도구와 Bash 읽기 명령 두 형태
    assert "Read(./secrets/.env)" in deny
    for reader in ("cat", "head", "tail", "less", "more"):
        for path in ("secrets/.env", "./secrets/.env"):
            assert _bash_denied(deny, f"{reader} {path}"), f"{reader} {path} 미거부"
    assert _bash_denied(deny, "grep KIS secrets/.env")
    # 절대경로·$PWD 형태도 막는다
    for cmd in (
        "cat /home/user/project/secrets/.env",
        'head -5 "$PWD/secrets/.env"',
        "tail -n 3 /home/user/project/secrets/.env",
        "grep KIS /home/user/project/secrets/.env",
        "sed -n 1p /home/user/project/secrets/.env",
    ):
        assert _bash_denied(deny, cmd), f"{cmd} 미거부"
    # accounts secrets import 가 만드는 백업(secrets/.env.bak-<시각>, 앱키 원문)도 막는다(FR-3)
    for cmd in (
        "cat secrets/.env.bak-20260101_000000",
        "cat /a/b/secrets/.env.bak-20260101_000000",
        "head -3 ./secrets/.env.bak-20260101_000000",
        'grep KIS "$PWD/secrets/.env.bak-20260101_000000"',
    ):
        assert _bash_denied(deny, cmd), f"{cmd} 미거부"
    assert _read_denied(deny, "secrets/.env.bak-20260101_000000")
    assert _read_denied(deny, "./secrets/.env")
    # 템플릿 예시는 Read 도구로 읽힌다(Bash 읽기는 과차단 수용, FR-3)
    assert not _read_denied(deny, "templates/secrets/.env.example")
    # 허용 명령은 막지 않는다
    for cmd in ALLOWED:
        assert not _bash_denied(deny, cmd), f"{cmd} 가 거부됨"
    # 사용자 전용 명령: uv run · python -m · uv run python -m 세 형태
    for cmd in USER_ONLY:
        assert f"Bash(uv run trader {cmd}*)" in deny, cmd
        assert f"Bash(python -m trader {cmd}*)" in deny, cmd
        assert f"Bash(uv run python -m trader {cmd}*)" in deny, cmd
        assert _bash_denied(deny, f"uv run python -m trader {cmd} --x"), cmd
    # 세션 시작 훅은 파이썬
    hooks = data["hooks"]["SessionStart"]
    commands = [h["command"] for entry in hooks for h in entry["hooks"]]
    # 훅은 표준 라이브러리만 쓴다 — 프로젝트 동기화 없이 돈다(M-6)
    assert any(
        "uv run --no-project python" in c and "tools/session_start.py" in c for c in commands
    )
    assert not any(c.endswith((".sh", ".bat", ".ps1")) for c in commands)
