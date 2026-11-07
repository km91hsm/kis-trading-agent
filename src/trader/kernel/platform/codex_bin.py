"""Codex CLI 발견. 조회는 모델이나 로그인 프로세스를 시작하지 않는다."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from trader.kernel.config import ConfigError


class CodexNotFound(ConfigError):
    pass


def find_codex(override: str | None = None) -> Path:
    candidates = (
        [Path(override).expanduser()]
        if override
        else [
            Path(p)
            for p in (
                (shutil.which("codex.cmd") if sys.platform.startswith("win") else None)
                or shutil.which("codex"),
                Path.home() / ".local/bin/codex",
                Path("/opt/homebrew/bin/codex"),
                Path("/usr/local/bin/codex"),
            )
            if p
        ]
    )
    for path in candidates:
        if path.is_file() and (sys.platform.startswith("win") or os.access(path, os.X_OK)):
            return path.absolute()
    raise CodexNotFound("Codex CLI를 찾을 수 없습니다. 설치 후 다음 새 분석을 실행하세요")


def child_env(bin_path: Path) -> dict[str, str]:
    """인증 저장소와 발견한 Node·OS 런타임만 보존한다. 작업·비밀 환경은 상속하지 않는다."""
    keep = ("HOME", "USERPROFILE", "SYSTEMROOT", "WINDIR", "APPDATA", "LOCALAPPDATA", "CODEX_HOME")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    parts = [str(bin_path.parent)]
    node = shutil.which("node")
    if node:
        parts.append(str(Path(node).parent))
    if sys.platform.startswith("win"):
        root = env.get("SYSTEMROOT") or env.get("WINDIR")
        if root:
            parts.extend((str(Path(root) / "System32"), root))
    else:
        parts.extend(os.defpath.split(os.pathsep))
    env["PATH"] = os.pathsep.join(dict.fromkeys(parts))
    return env
