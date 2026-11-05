"""`trader app`: 번들 더블클릭 진입점. 한 프로세스에서 데몬(스케줄러) + 웹 UI(uvicorn 스레드)를
띄우고 브라우저를 열거나(기본) 메뉴바/작업표시줄 트레이로 제어한다(`--tray`).

- 데몬은 `Daemon.start(install_signals=False)` — 시그널은 여기서 받아 `stop_event` 만 세운다.
  잡은 스케줄러 스레드에서 돈다. 메인 스레드는 트레이(pystray 는 메인 스레드 필수) 또는 대기.
- 웹은 `build_web_deps` 그대로(주문 집행 포트 없음)를 `trader-web` 스레드의 `uvicorn.Server` 로.
- 종료 순서: 웹 `should_exit` → `Daemon.shutdown(wait=True)`(실행 중 잡 대기) → 0.
- uvicorn·웹 앱은 함수 안에서만 import 한다(CLI 기동 비용, PF-2).
"""

from __future__ import annotations

import logging
import signal
import socket
import threading
import time
import webbrowser
from typing import Any

import typer

from trader.kernel.cli.boot import Boot, boot, config_errors
from trader.kernel.config.secrets import ensure_web_token
from trader.kernel.daemon.runner import Daemon
from trader.kernel.platform import bundle, paths, procs
from trader.kernel.platform import tray as tray_mod
from trader.kernel.platform.tray import TrayUnavailable, run_tray

logger = logging.getLogger("trader.cli")

PORT_WAIT_S = 20.0  # 첫 실행은 무거운 import 로 오래 걸린다 — 포트가 열릴 때까지 최대 이만큼
WEB_JOIN_S = 10.0


def wait_for_port(
    host: str, port: int, *, timeout_s: float, stop: threading.Event | None = None
) -> bool:
    """host:port 가 연결을 받을 때까지 기다린다 (최대 timeout_s). stop 이 세워지면 False."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if stop is not None and stop.is_set():
            return False
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.25)
    return False


class WebThread:
    """uvicorn.Server 를 `trader-web` 스레드에서 돌린다. 포트 점유 등으로 서버가 못 뜨면
    스레드만 조용히 끝나므로 로그로 드러내고 `finished` 를 세운다."""

    def __init__(self, app: Any, host: str, port: int) -> None:
        import uvicorn

        self.host, self.port = host, port
        self.server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=host,
                port=port,
                # 접근 로그는 실시간 시세 폴링마다 쌓여 끈다
                access_log=False,
                # 기본 로그 설정은 sys.stdout.isatty() 를 부른다(창 없는 앱은 None) — 로그는
                # 부트가 건 파일 핸들러(root)로 간다
                log_config=None,
                use_colors=False,
                # X-Forwarded-For 로 접속 주소를 바꾸지 않는다(Tailscale 게이트가 실제 피어를 본다)
                proxy_headers=False,
            )
        )
        self.finished = threading.Event()
        self._stopping = False
        self._thread = threading.Thread(target=self._run, name="trader-web", daemon=True)

    def _run(self) -> None:
        try:
            self.server.run()
        except BaseException as e:  # noqa: BLE001 - SystemExit(포트 점유)·소켓 오류 포함
            logger.error("웹 UI 서버 종료 %s:%s: %s", self.host, self.port, e)
        finally:
            if not self._stopping:
                logger.error("웹 UI 서버가 멈췄습니다 (%s:%s)", self.host, self.port)
            self.finished.set()

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = WEB_JOIN_S) -> None:
        self._stopping = True
        self.server.should_exit = True
        if self._thread.is_alive():
            self._thread.join(timeout)


def _toggle_pause_fn(boot_: Boot, conn_factory):
    """트레이 일시정지 토글: accounts 포트에 `set_paused` 가 있으면 그것(control_log, channel=tray),
    없으면 커널 기본(control 플래그)."""
    port = (boot_.ports or {}).get("accounts")
    setter = getattr(port, "set_paused", None)
    set_fn = (lambda c, v: setter(c, v, channel="tray")) if callable(setter) else None
    return lambda: tray_mod.toggle_pause(conn_factory, set_fn=set_fn)


def _state_fn(boot_: Boot, conn_factory, url: str):
    port = (boot_.ports or {}).get("accounts")
    paused_fn = getattr(port, "paused", None)
    return lambda: tray_mod.read_state(
        conn_factory, url, paused_fn=paused_fn if callable(paused_fn) else None
    )


def run_app(
    boot_: Boot,
    *,
    url: str,
    tray: bool,
    open_browser: bool,
    browser_delay: float,
    dry_run: bool,
    daemon_factory=Daemon,
    tray_runner=run_tray,
) -> int:
    """데몬 → 웹 스레드 → (브라우저) → 트레이 또는 대기 → 웹 stop → 데몬 shutdown. 종료 코드."""
    from trader.kernel.cli.main import build_daemon_deps, build_web_deps
    from trader.kernel.web.app import LOOPBACK_HOSTS, create_app

    host, port = boot_.settings.web.bind, boot_.settings.web.port
    if host not in LOOPBACK_HOSTS:
        logger.warning("웹 서버를 루프백 밖(%s)에 바인드합니다 — tailscale serve 를 쓰세요", host)
    d = daemon_factory(boot_.features, build_daemon_deps(boot_, dry_run=dry_run))
    if not d.start(install_signals=False):
        typer.echo("다른 데몬이 실행 중입니다", err=True)
        return 1
    stop = d.deps.stop_event
    web: WebThread | None = None
    guard = threading.Lock()
    done = {"down": False}

    def shutdown() -> None:
        """멱등: 웹 → 데몬(wait=True). 트레이 종료 메뉴(워커 스레드)·메인 스레드 둘 다 부른다."""
        with guard:
            if done["down"]:
                return
            done["down"] = True
        stop.set()
        try:
            if web is not None:
                web.stop()
        finally:
            d.shutdown(wait=True)

    def on_signal(signum, _frame=None) -> None:
        logger.info("시그널 %s 수신, 웹·데몬을 종료합니다", signum)
        stop.set()  # 대기 루프·트레이 갱신 스레드가 보고 순서대로 내린다

    old_handlers: dict[int, Any] = {}
    release_watch = lambda: None  # noqa: E731
    try:
        web = WebThread(create_app(build_web_deps(boot_)), host, port)
        web.start()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                old_handlers[sig] = signal.signal(sig, on_signal)
            except (ValueError, OSError):  # 메인 스레드가 아니면 등록 불가
                pass
        if open_browser:

            def open_when_ready() -> None:
                if browser_delay > 0 and stop.wait(browser_delay):
                    return
                if wait_for_port(host, port, timeout_s=PORT_WAIT_S, stop=stop):
                    webbrowser.open(url)

            opener = threading.Thread(
                target=open_when_ready, name="trader-open-browser", daemon=True
            )
            opener.start()
        if tray:
            # 메인 스레드가 AppKit 런루프(icon.run)에 막혀도 시그널을 받게 wakeup fd 감시
            release_watch = tray_mod.watch_signals(on_signal, (signal.SIGINT, signal.SIGTERM))
            conn_factory = d.deps.conn_factory
            try:
                tray_runner(
                    state_fn=_state_fn(boot_, conn_factory, url),
                    on_toggle_pause=_toggle_pause_fn(boot_, conn_factory),
                    on_open_web=lambda: webbrowser.open(url),
                    on_open_logs=lambda: procs.open_folder(paths.state_dir() / "logs"),
                    on_quit=shutdown,
                    icon_dir=bundle.icons_dir(),
                    stop_event=stop,
                    finished=web.finished,
                )
            except TrayUnavailable as e:
                typer.echo(f"트레이 없이 실행합니다 ({e}). 종료는 Ctrl+C.")
                tray = False
        if not tray:
            typer.echo(f"웹 UI: {url}  (종료: Ctrl+C)")
            # 타임아웃 없는 wait 는 Windows 에서 SIGINT 로 깨지 않는다
            while not stop.wait(1.0):
                if web.finished.is_set():
                    break
        failed = web.finished.is_set() and not web._stopping
    finally:
        release_watch()
        shutdown()
        for sig, h in old_handlers.items():
            try:
                signal.signal(sig, h)
            except (ValueError, OSError, TypeError):
                pass
    if failed:
        typer.echo("오류: 웹 UI 서버가 멈췄습니다 (포트 사용 중 등 — 로그 참고)", err=True)
        return 1
    return 0


@config_errors
def app(
    no_browser: bool = typer.Option(False, "--no-browser", help="브라우저를 열지 않음"),
    browser_delay: float = typer.Option(1.5, "--browser-delay", help="웹 기동 뒤 브라우저까지 초"),
    dry_run: bool = typer.Option(False, "--dry-run", help="주문 없이 실행"),
    tray: bool = typer.Option(
        False, "--tray", help="메뉴바/작업표시줄 아이콘으로 제어 (메인 스레드는 트레이)"
    ),
) -> None:
    """번들 더블클릭 진입점: 첫 실행 초기화 → 데몬 + 웹 UI → 브라우저(또는 트레이)."""
    created = bundle.first_run_init(paths.project_root())
    if created:
        typer.echo("초기화: " + ", ".join(created))
    ensure_web_token()
    b = boot(ports=True, schedule=True, log_name="app")
    try:
        url = f"http://{b.settings.web.bind}:{b.settings.web.port}"
        code = run_app(
            b,
            url=url,
            tray=tray,
            open_browser=not no_browser,
            browser_delay=browser_delay,
            dry_run=dry_run,
            daemon_factory=Daemon,
            tray_runner=run_tray,
        )
    finally:
        if b.conn is not None:
            b.conn.close()
    if code:
        raise typer.Exit(code=code)
