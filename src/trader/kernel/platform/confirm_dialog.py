"""OS 기본 확인창(예/아니오). macOS 는 osascript, Windows 는 MessageBoxW.

반환: True=확인, False=취소, None=띄우지 못함(호출자가 알아서 정한다 — 긴급 정지는 막지 않는다).
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
            r = run(["osascript", "-e", script], capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode == 0:
            return True
        return False if r.returncode == 1 else None
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
