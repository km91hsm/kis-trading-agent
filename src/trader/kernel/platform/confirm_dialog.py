"""OS 기본 확인창(예/아니오). macOS 는 osascript, Windows 는 MessageBoxW.

반환: True=확인, False=취소 또는 답 없음(시간 초과 — 자리를 비운 사이 바꾸지 않는다),
None=띄우지 못함(실행 실패·스크립트 오류 — 호출자가 정한다, 긴급 정지는 막지 않는다).
macOS osascript 는 취소(-128)와 스크립트 오류(예: -1713 상호작용 불가)를 모두 rc 1 로 내므로
stderr 의 "-128" 로 가른다.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from typing import Any

MB_YESNO = 0x4
MB_ICONWARNING = 0x30
MB_DEFBUTTON2 = 0x100
MB_TOPMOST = 0x40000
IDYES = 6


def _applescript_str(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def ask_yes_no(
    title: str,
    message: str,
    *,
    platform: str = sys.platform,
    run: Callable[..., Any] = subprocess.run,
    message_box: Callable[..., int] | None = None,
) -> bool | None:
    if platform == "darwin":
        script = (
            f"display dialog {_applescript_str(message)} with title {_applescript_str(title)} "
            'buttons {"취소","일시정지"} default button "취소" cancel button "취소" '
            "with icon caution"
        )
        try:
            r = run(
                ["osascript", "-e", script],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            return False  # 창은 떴지만 답이 없다 — 바꾸지 않는다
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode == 0:
            return True
        if r.returncode == 1 and "-128" in (r.stderr or ""):
            return False  # 사용자 취소
        return None
    if platform == "win32":
        try:
            if message_box is None:
                import ctypes

                message_box = ctypes.windll.user32.MessageBoxW  # type: ignore[attr-defined]
            flags = MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2 | MB_TOPMOST
            return message_box(0, message, title, flags) == IDYES
        except Exception:  # noqa: BLE001
            return None
    return None
