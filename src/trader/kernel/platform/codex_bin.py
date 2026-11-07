"""Codex CLI 발견. 조회는 모델이나 로그인 프로세스를 시작하지 않는다."""

from __future__ import annotations

import os
import shutil
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
                shutil.which("codex"),
                Path.home() / ".local/bin/codex",
                Path("/opt/homebrew/bin/codex"),
                Path("/usr/local/bin/codex"),
            )
            if p
        ]
    )
    for path in candidates:
        if path.is_file() and (os.name == "nt" or os.access(path, os.X_OK)):
            return path.resolve()
    raise CodexNotFound("Codex CLI를 찾을 수 없습니다. 설치 후 다음 새 분석을 실행하세요")
