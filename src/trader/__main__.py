"""`python -m trader` / 번들 실행파일 진입점.

순서: (1) Chrome 네이티브 메시징 origin(`chrome-extension://…/`)이면 CLI 를 거치지 않고 세이브티커
네이티브 호스트로만 간다(바이너리 프레이밍 보호 — 커널 밖 기능을 import 하는 유일한 예외, 함수 안
지연 import). (2) 창 없는 실행파일의 None 스트림을 채운다. (3) 개발 환경이거나 인자가 있으면 보통
CLI. (4) 번들을 인자 없이(더블클릭) 실행하면 `trader app`(창 없는 TraderApp 은 `app --tray`).
Windows 더블클릭은 오류로 끝나면 콘솔이 바로 닫혀 메시지를 못 보므로 종료 전에 Enter 를 기다린다."""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

TRANSLOCATED_MSG = (
    "macOS 가 앱을 격리된 임시 경로(App Translocation)에서 실행했어요 — 이 경로가 로그인 시 "
    "자동 시작·확장 호스트 등록에 박히면 다음 실행에서 깨집니다. "
    "Finder 에서 Trader.app 을 응용 프로그램(/Applications) 폴더로 옮긴 뒤 실행하세요."
)


def translocated(path: str) -> bool:
    """App Translocation 경로에서 실행 중인지 (`bundle.is_translocated`)."""
    from trader.kernel.platform import bundle

    return bundle.is_translocated(path)


def _ensure_streams() -> None:
    """창 없는 실행파일(Windows TraderApp)은 stdout/stderr 가 None 이다 — 그대로 두면
    `print`·`isatty()`·라이브러리 로그 설정이 AttributeError 로 죽는다. devnull 로 바꿔 둔다."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115


def _record_startup_error(text: str) -> None:
    """창 없는 앱의 시작 실패는 화면·콘솔 어디에도 안 보인다 — state/logs/startup-error.log 에
    남기고(데이터 루트를 못 얻으면 `user_data_dir()/state/logs`, 그것도 안 되면 홈) 가능하면 OS
    알림을 띄운다."""
    for log_dir in _startup_log_dirs():
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            with (log_dir / "startup-error.log").open("a", encoding="utf-8") as f:
                f.write(text + "\n")
            break
        except OSError:
            continue
    try:
        from trader.kernel.platform import notify_os

        lines = text.strip().splitlines() or [""]
        notify_os.alert("Trader 시작 실패", lines[-1][:200])
    except Exception:  # noqa: BLE001
        pass


def _startup_log_dirs() -> list[Path]:
    """startup-error.log 후보 폴더 순서: state_dir → user_data_dir()/state/logs → 홈."""
    dirs: list[Path] = []
    try:
        from trader.kernel.platform import paths

        dirs.append(paths.state_dir() / "logs")
    except Exception:  # noqa: BLE001 - 경로 자체가 실패 원인일 수 있다
        pass
    try:
        from trader.kernel.platform import bundle

        dirs.append(bundle.user_data_dir() / "state" / "logs")
    except Exception:  # noqa: BLE001
        pass
    dirs.append(Path.home())
    return dirs


def _pause_if_console() -> None:
    if sys.platform != "win32" or sys.stdin is None:
        return
    if getattr(sys.stdin, "isatty", lambda: False)():
        try:
            input("오류로 종료됐습니다. Enter 를 누르면 닫힙니다")
        except EOFError:
            pass


def main() -> None:
    # (1) Chrome 은 호출한 확장 origin 을 argv[1] 로 준다 — CLI 로깅·출력이 프레이밍을 깨지 않게
    # 접두만 먼저 보고, 그때만 기능 모듈을 import 한다(보통 CLI 기동 비용, T11)
    if len(sys.argv) > 1 and sys.argv[1].startswith("chrome-extension://"):
        from trader.features.saveticker.bridge_install import is_native_origin, native_entry

        if is_native_origin(sys.argv[1]):
            code = native_entry(sys.argv[1])
            if code:
                raise SystemExit(code)
            return
    # (2)
    _ensure_streams()
    from trader.kernel.cli import main as cli
    from trader.kernel.platform import bundle

    # (3) UTF-8 강제는 TraderApp.__call__ 이 한다
    if not (bundle.is_frozen() and len(sys.argv) == 1):
        cli.app()
        return
    # (4) 창 없는 실행파일(TraderApp)은 트레이 모드, 콘솔 trader 는 터미널 모드
    windowed = Path(sys.executable).stem.lower().startswith("traderapp")
    if translocated(sys.executable):
        print(TRANSLOCATED_MSG, file=sys.stderr)
        _record_startup_error(TRANSLOCATED_MSG)
        raise SystemExit(1)
    try:
        cli.app(["app", "--tray"] if windowed else ["app"])
    except SystemExit as e:
        if e.code not in (0, None):
            if windowed:
                _record_startup_error(
                    f"종료 코드 {e.code} — 설정 오류·데몬 잠금 등 (trader app 로그 참고)"
                )
            _pause_if_console()
        raise
    except Exception:
        traceback.print_exc()
        if windowed:
            _record_startup_error(traceback.format_exc())
        _pause_if_console()
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
