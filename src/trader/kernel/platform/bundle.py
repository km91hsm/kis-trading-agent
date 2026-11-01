"""PyInstaller 번들(frozen) 분기는 이 모듈 한 곳에서만 답한다.

- 실행파일 폴더(macOS `.app` 은 그 부모 폴더)가 TRADER_HOME 이다 (config/ secrets/ state/ docs/
  하위 폴더, 폴더째 이동 가능).
- 런타임 리소스(에이전트 md·웹 dist·설정 템플릿)는 `_internal/`(sys._MEIPASS) 아래에 있다.
- 세션의 도구 명령: frozen 이면 `trader tool`, 개발 환경이면 `python -m trader tool`.
"""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def executable() -> Path:
    """실행파일 경로. 번들은 실경로로(심링크 없음), 개발 환경은 venv 의 python 그대로(resolve 하면
    venv 심링크가 시스템 python 으로 풀려 PATH 앞에 둘 디렉터리가 venv bin 이 아니게 된다)."""
    exe = Path(sys.executable)
    return exe.resolve() if is_frozen() else exe


def cli_executable() -> Path:
    """콘솔 CLI 실행파일. 창 없는 TraderApp 으로 떠도 세션의 `trader tool`·launchd 의
    `trader daemon` 은 이것을 쓴다 (같은 폴더의 trader[.exe]; 없으면 자기 자신)."""
    exe = executable()
    if not is_frozen():
        return exe
    suffix = ".exe" if sys.platform == "win32" else ""
    cli = exe.parent / f"trader{suffix}"
    return cli.resolve() if cli.exists() else exe


def app_bundle_dir() -> Path | None:
    """macOS `.app` 안에서 돌고 있으면 그 `.app` 경로 (Contents/MacOS 아래 실행파일일 때),
    아니면 None."""
    if not is_frozen():
        return None
    exe = executable()
    for p in exe.parents:
        if p.suffix == ".app" and (p / "Contents" / "MacOS") in exe.parents:
            return p
    return None


def bundle_root() -> Path:
    """TRADER_HOME: `.app` 이면 그 부모 폴더(config/secrets/state 가 .app 옆), 아니면 실행파일 폴더,
    개발 환경은 저장소 루트."""
    if is_frozen():
        app = app_bundle_dir()
        return app.parent if app is not None else executable().parent
    from trader.kernel.platform import paths

    return paths.project_root()


def resource_dir() -> Path:
    """번들 리소스 루트: frozen 이면 `_internal/`(sys._MEIPASS), 아니면 저장소 루트."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", executable().parent / "_internal")).resolve()
    from trader.kernel.platform import paths

    return paths.project_root()


def tool_command() -> str:
    """오케스트레이터 세션의 도구 명령 접두 (`--allowedTools` 와 프롬프트가 같은 문자열을 쓴다)."""
    return "trader tool" if is_frozen() else "python -m trader tool"


def cli_argv() -> list[str]:
    """`trader <명령>` 을 하위 프로세스로 띄울 때의 접두 argv: 번들은 콘솔 CLI, 개발 환경은
    `python -m trader` (번들의 sys.executable 은 `-m` 을 모른다)."""
    if is_frozen():
        return [str(cli_executable())]
    return [sys.executable, "-m", "trader"]


def templates_dir() -> Path:
    """첫 실행 템플릿(config yaml·.env.example).

    번들은 `_internal/templates`, 개발은 소스 트리 `templates/`(TRADER_HOME 과 무관)."""
    if is_frozen():
        return resource_dir() / "templates"
    return Path(__file__).resolve().parents[4] / "templates"
