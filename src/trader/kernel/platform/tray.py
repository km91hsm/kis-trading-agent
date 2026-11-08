"""트레이/메뉴바 아이콘. 메뉴 모델·제목은 순수 함수, pystray·PIL 은 run_tray·icon_image 안에서만
import 한다.

- macOS 는 메뉴바, Windows 는 작업표시줄 알림 영역. 메뉴: 웹 UI 열기 / 일시정지·재개 /
  로그 폴더 열기 / 데이터 폴더 열기 / 로그인 시 자동 시작(체크) / 종료. 일시정지·재개는 누를 때
  원장 값을 다시 읽어 반대 값을 **명시적으로** 쓴다(뒤집기 아님 — 캐시가 낡아도 엉뚱한 쪽으로
  가지 않는다). 일시정지를 켜기 직전엔 OS 확인창(confirm_dialog)을 띄운다 — 취소·무응답이면
  그대로, 못 띄우면 막지 않고 진행, 재개는 묻지 않는다. 창이 떠 있는 동안 다시 누르면 무시한다.
- pystray 의 Icon.run() 은 **메인 스레드**에서 돌아야 한다(macOS AppKit). 데몬은
  트레이 진입점이 백그라운드 스레드에 둔다.
- 일시정지는 원장 control 의 `paused` 플래그다. 커널은 기능을 모르므로 기본은 플래그를 직접
  읽고 쓰며, 호출자가 `paused_fn`·`set_fn` 으로 기능 포트(control_log 기록 등)를 주입한다.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trader.kernel.ledger.control import get_flag, set_flag
from trader.kernel.platform.confirm_dialog import ask_yes_no

logger = logging.getLogger("trader.platform")


class TrayUnavailable(RuntimeError):
    """pystray(또는 macOS pyobjc)가 없어 트레이를 띄울 수 없다."""


@dataclass(frozen=True)
class TrayState:
    paused: bool
    pending_approvals: int
    last_error: str | None
    web_url: str
    autostart: bool = False  # 로그온 등록(installer.installed) 여부
    autostart_available: bool = True  # App Translocation 등에서는 등록 불가(메뉴 비활성)


@dataclass(frozen=True)
class MenuItem:
    key: str
    label: str
    enabled: bool = True
    checked: bool = False


def menu_model(state: TrayState) -> list[MenuItem]:
    return [
        MenuItem("open_web", "웹 UI 열기"),
        MenuItem("toggle_pause", "재개" if state.paused else "일시정지", checked=state.paused),
        MenuItem("open_logs", "로그 폴더 열기"),
        MenuItem("open_data", "데이터 폴더 열기"),
        MenuItem(
            "autostart",
            "로그인 시 자동 시작",
            enabled=state.autostart_available,
            checked=state.autostart,
        ),
        MenuItem("quit", "종료"),
    ]


def title_text(state: TrayState) -> str:
    """툴팁/제목: 일시정지 > 미승인 n > 실행 중."""
    if state.paused:
        return "trader · 일시정지"
    if state.pending_approvals > 0:
        return f"trader · 미승인 {state.pending_approvals}"
    return "trader · 실행 중"


def _default_paused(conn: sqlite3.Connection) -> bool:
    return bool(get_flag(conn, "paused", False))


def _default_set_paused(conn: sqlite3.Connection, value: bool) -> None:
    set_flag(conn, "paused", value)


def read_state(
    conn_factory: Callable[[], sqlite3.Connection],
    web_url: str,
    *,
    paused_fn: Callable[[sqlite3.Connection], bool] | None = None,
    close: bool = True,
) -> TrayState:
    conn = conn_factory()
    try:
        paused = (paused_fn or _default_paused)(conn)
        pending = conn.execute("SELECT COUNT(*) FROM approvals WHERE status='pending'").fetchone()[
            0
        ]
        row = conn.execute(
            "SELECT error FROM runs WHERE status='error' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        last_error = row[0] if row else None
    finally:
        if close:
            conn.close()
    return TrayState(
        paused=bool(paused), pending_approvals=int(pending), last_error=last_error, web_url=web_url
    )


def set_pause(
    conn_factory: Callable[[], sqlite3.Connection],
    value: bool,
    *,
    set_fn: Callable[[sqlite3.Connection, bool], None] | None = None,
    close: bool = True,
) -> None:
    """paused 플래그에 `value` 를 쓴다 (웹·텔레그램·CLI 와 같은 플래그). 뒤집지 않는다.
    `set_fn(conn, value)` 가 주어지면 기본 `set_flag` 대신 그것으로 쓴다."""
    conn = conn_factory()
    try:
        (set_fn or _default_set_paused)(conn, value)
    finally:
        if close:
            conn.close()


def icon_image(icon_dir: Path, *, dark_menubar: bool):
    """메뉴바 배경에 맞는 단색 ▲ (밝은 메뉴바 → 어두운 글리프)."""
    try:
        from PIL import Image
    except Exception as e:  # noqa: BLE001 - PIL 없음(pystray 전이 의존)
        raise TrayUnavailable(f"트레이 아이콘을 읽을 수 없습니다: {e}") from e

    name = "tray-light.png" if dark_menubar else "tray-dark.png"
    return Image.open(icon_dir / name)


def watch_signals(on_signal: Callable[[int], None], signals) -> Callable[[], None]:
    """메인 스레드가 AppKit 런루프(icon.run)에 막혀 있어도 시그널을 받는다.

    파이썬 핸들러는 메인 스레드가 바이트코드를 돌릴 때만 실행되므로, C 레벨 핸들러가 쓰는
    wakeup fd(소켓쌍)를 감시 스레드가 읽어 시그널 번호를 `on_signal` 로 넘긴다. 핸들러는
    호출자가 `signal.signal` 로 따로 설치해야 한다. 반환값은 해제 함수(이전 wakeup fd 복원)."""
    import signal
    import socket

    wanted = {int(s) for s in signals}
    r, w = socket.socketpair()
    w.setblocking(False)
    try:
        prev = signal.set_wakeup_fd(w.fileno(), warn_on_full_buffer=False)
    except ValueError:  # 메인 스레드가 아니면 설정할 수 없다
        r.close()
        w.close()
        return lambda: None

    def watch() -> None:
        while True:
            try:
                data = r.recv(64)
            except OSError:
                return
            if not data:
                return
            for signum in data:
                if signum in wanted:
                    try:
                        on_signal(signum)
                    except Exception:  # noqa: BLE001 - 감시 스레드는 죽지 않는다
                        logger.exception("시그널 처리 실패")

    threading.Thread(target=watch, name="trader-signal-watch", daemon=True).start()

    def release() -> None:
        try:
            signal.set_wakeup_fd(prev)
        except ValueError:
            pass
        w.close()
        try:
            r.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        r.close()

    return release


def on_main(fn: Callable[[], None]) -> None:
    """AppKit 객체(아이콘 제목·메뉴·stop)는 메인 스레드에서만 만진다. macOS 에서 PyObjC 의
    callAfter 가 있으면 런루프에 넘기고, 없거나 다른 OS 면 그대로 부른다."""
    if sys.platform == "darwin":
        try:
            from PyObjCTools import AppHelper

            AppHelper.callAfter(fn)
            return
        except Exception:  # noqa: BLE001 - pyobjc 없음 → 직접 호출
            pass
    fn()


class TrayController:
    """pystray 콜백 뒤의 상태 기계. 메뉴 라벨·checked 는 **캐시된 상태**만 읽고(메인 스레드에서
    SQLite 를 열지 않는다), 토글·종료는 워커 스레드에서 돌려 AppKit 런루프를 막지 않는다.
    `icon` 은 `.title`·`.stop()`·`.update_menu()` 만 쓰므로 테스트는 가짜 객체를 넣는다."""

    def __init__(
        self,
        *,
        state_fn: Callable[[], TrayState],
        on_set_pause: Callable[[bool], None],
        on_quit: Callable[[], None],
        initial: TrayState,
        run_on_main: Callable[[Callable[[], None]], None] = on_main,
        on_toggle_autostart: Callable[[], None] | None = None,
        confirm_pause: Callable[[], bool | None] | None = None,
    ) -> None:
        self._confirm_pause = confirm_pause
        self._state_fn = state_fn
        self._on_set_pause = on_set_pause
        self._pause_busy = threading.Lock()  # 확인창이 떠 있는 동안 두 번째 클릭은 무시
        self._on_toggle_autostart = on_toggle_autostart
        self._on_quit = on_quit
        self._run_on_main = run_on_main
        self.state = initial
        self.quitting = threading.Event()
        self.icon: Any = None

    # --- 읽기(메인 스레드, 캐시만) ---
    def label(self, key: str) -> str:
        return next(i.label for i in menu_model(self.state) if i.key == key)

    def checked(self, key: str) -> bool:
        return next(i.checked for i in menu_model(self.state) if i.key == key)

    def enabled(self, key: str) -> bool:
        return next(i.enabled for i in menu_model(self.state) if i.key == key)

    def title(self) -> str:
        return QUITTING_TITLE if self.quitting.is_set() else title_text(self.state)

    # --- 갱신(워커 스레드) ---
    def refresh_state(self) -> None:
        self.state = self._state_fn()
        self._push_title()

    def _push_title(self) -> None:
        icon = self.icon
        if icon is None:
            return
        text = self.title()

        def apply() -> None:
            try:
                icon.title = text
                update = getattr(icon, "update_menu", None)
                if update is not None:
                    update()
            except Exception:  # noqa: BLE001
                logger.debug("트레이 제목 갱신 실패", exc_info=True)

        self._run_on_main(apply)

    def stop_icon(self) -> None:
        icon = self.icon
        if icon is None:
            return
        self._run_on_main(icon.stop)

    # --- 메뉴 액션(메인 스레드에서 호출되지만 즉시 돌아온다) ---
    def quit_action(self, icon: Any = None, _item: Any = None) -> None:
        if self.quitting.is_set():
            return
        self.quitting.set()
        self._push_title()

        def work() -> None:
            try:
                self._on_quit()
            except Exception:  # noqa: BLE001
                logger.exception("트레이 종료 처리 실패")
            finally:
                self.stop_icon()

        threading.Thread(target=work, name="trader-tray-quit", daemon=True).start()

    def pause_action(self, icon: Any = None, _item: Any = None) -> None:
        if not self._pause_busy.acquire(blocking=False):
            return  # 앞선 클릭(확인창)이 아직 끝나지 않았다

        def work() -> None:
            try:
                # 캐시(최대 poll_s 낡음)가 아니라 지금 원장 값으로 정한다
                target = not self._state_fn().paused
                if target and self._confirm_pause is not None:
                    # 켜기 직전만 확인. 취소·무응답(False)이면 그대로, 못 띄우면(None) 진행.
                    if self._confirm_pause() is False:
                        return
                self._on_set_pause(target)
                self.refresh_state()
            except Exception:  # noqa: BLE001 - 원장 잠금 등
                logger.exception("일시정지 전환 실패")
            finally:
                self._pause_busy.release()

        threading.Thread(target=work, name="trader-tray-pause", daemon=True).start()

    def autostart_action(self, icon: Any = None, _item: Any = None) -> None:
        """로그인 시 자동 시작 토글(launchctl·schtasks 는 느릴 수 있어 워커 스레드)."""
        toggle = self._on_toggle_autostart
        if toggle is None or not self.state.autostart_available:
            return

        def work() -> None:
            try:
                toggle()
            except Exception:  # noqa: BLE001 - 등록 명령 실패 등
                logger.exception("로그인 시 자동 시작 토글 실패")
            try:
                self.refresh_state()
            except Exception:  # noqa: BLE001
                logger.debug("트레이 상태 갱신 실패", exc_info=True)

        threading.Thread(target=work, name="trader-tray-autostart", daemon=True).start()


PAUSE_CONFIRM_MESSAGE = (
    "자동매매를 전체 일시정지할까요? 새 매수·진입과 승인이 멈춥니다"
    "(손절·청산은 계속, 계좌 모드는 그대로). "
    "앱을 끄려면 「종료」를 누르세요."
)
QUITTING_TITLE = "trader · 종료 중"
STOPPED_TITLE = "trader · 데몬 중단"


def run_tray(
    *,
    state_fn: Callable[[], TrayState],
    on_set_pause: Callable[[bool], None],
    on_open_web: Callable[[], None],
    on_open_logs: Callable[[], None],
    on_quit: Callable[[], None],
    icon_dir: Path,
    poll_s: float = 30.0,
    stop_event: threading.Event | None = None,
    finished: threading.Event | None = None,
    on_open_data: Callable[[], None] | None = None,
    on_toggle_autostart: Callable[[], None] | None = None,
) -> None:
    """메인 스레드에서 아이콘을 띄우고 종료 메뉴·on_quit 까지 블로킹한다. 돌아오면 아이콘은
    내려간 뒤다. `on_quit` 은 워커 스레드에서 돌고 그동안 제목이 '종료 중' 이다(런루프 유지).
    `finished` 가 서면(데몬 스레드 종료) 제목을 바꾸고 아이콘을 내린다.
    `on_open_data`·`on_toggle_autostart` 가 없으면 그 메뉴 항목은 빠진다."""
    try:
        import pystray
    except Exception as e:  # ImportError 외에 macOS 에서 AppKit 바인딩 오류도 온다
        raise TrayUnavailable(f"트레이를 띄울 수 없습니다: {e}") from e

    ctl = TrayController(
        state_fn=state_fn,
        on_set_pause=on_set_pause,
        on_quit=on_quit,
        initial=state_fn(),
        on_toggle_autostart=on_toggle_autostart,
        confirm_pause=lambda: ask_yes_no("trader", PAUSE_CONFIRM_MESSAGE),
    )

    def label(key: str):
        return lambda _item: ctl.label(key)

    items = [
        pystray.MenuItem(label("open_web"), lambda icon, it: on_open_web()),
        pystray.MenuItem(
            label("toggle_pause"), ctl.pause_action, checked=lambda _it: ctl.checked("toggle_pause")
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(label("open_logs"), lambda icon, it: on_open_logs()),
    ]
    if on_open_data is not None:
        items.append(pystray.MenuItem(label("open_data"), lambda icon, it: on_open_data()))
    if on_toggle_autostart is not None:
        items.append(
            pystray.MenuItem(
                label("autostart"),
                ctl.autostart_action,
                checked=lambda _it: ctl.checked("autostart"),
                enabled=lambda _it: ctl.enabled("autostart"),
            )
        )
    menu = pystray.Menu(
        *items,
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(label("quit"), ctl.quit_action),
    )
    icon = pystray.Icon(
        "trader", icon_image(icon_dir, dark_menubar=False), title=ctl.title(), menu=menu
    )
    ctl.icon = icon
    stop = threading.Event()

    def refresh():
        tick = min(poll_s, 1.0)
        waited = 0.0
        while not stop.wait(tick):
            if finished is not None and finished.is_set():  # 데몬 스레드가 끝났다(정상·오류)
                if not ctl.quitting.is_set():
                    ctl.quitting.set()
                    on_main(lambda t=STOPPED_TITLE: setattr(icon, "title", t))
                ctl.stop_icon()
                return
            if stop_event is not None and stop_event.is_set():  # 시그널 등 바깥에서 종료 요청
                ctl.quit_action()
                return
            waited += tick
            if waited < poll_s:
                continue
            waited = 0.0
            try:
                ctl.refresh_state()
            except Exception:  # 원장 잠금 등 일시 오류는 다음 틱에
                logger.debug("트레이 상태 갱신 실패", exc_info=True)

    threading.Thread(target=refresh, name="trader-tray-refresh", daemon=True).start()
    try:
        icon.run()
    finally:
        stop.set()
