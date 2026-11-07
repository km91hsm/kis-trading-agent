"""`trader` 루트 typer 앱: 커널 명령과 기능이 노출한 하위 명령을 묶는다."""

from __future__ import annotations

import logging
import sys
import threading
import time
from datetime import UTC, datetime

import typer

from trader import __version__
from trader.kernel.cli import (
    app_cmd,
    init_cmd,
    install_cmd,
    migrate_cmd,
    selfcheck,
    status_cmd,
    watchdog_cmd,
)
from trader.kernel.cli.boot import Boot, boot, config_errors
from trader.kernel.cli.dev import dev_app
from trader.kernel.cli.jobs_cmd import build_jobs_app
from trader.kernel.cli.tool_cmd import build_tool_app
from trader.kernel.config import KST, load_secrets
from trader.kernel.config.secrets import ensure_web_token
from trader.kernel.daemon.runner import Daemon, DaemonDeps
from trader.kernel.ledger import db
from trader.kernel.notify import NullNotifier
from trader.kernel.notify.commands import CommandContext, poll_once
from trader.kernel.notify.record import ledger_recorder
from trader.kernel.notify.telegram import TelegramNotifier, make_notifier, make_receiver
from trader.kernel.platform import paths
from trader.kernel.registry import discover, hooks
from trader.kernel.registry.types import Feature
from trader.kernel.web.deps import WebDeps

logger = logging.getLogger("trader.cli")


def force_utf8() -> None:
    """콘솔 출력을 UTF-8 로 고정한다 (Windows cp949 대비, 창 없는 번들의 None 스트림 허용)."""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


class TraderApp(typer.Typer):
    """콘솔 스크립트(`trader`)·`python -m trader` 가 부르는 `app()` 진입에서만 UTF-8 을 고정한다.
    (CliRunner 는 __call__ 을 거치지 않으므로 테스트 캡처 스트림은 건드리지 않는다.)"""

    def __call__(self, *args, **kwargs):
        force_utf8()
        return super().__call__(*args, **kwargs)


def build_daemon_deps(boot_: Boot, *, dry_run: bool) -> DaemonDeps:
    secrets = boot_.secrets or load_secrets()
    deps = DaemonDeps(
        settings=boot_.settings,
        schedule=boot_.schedule,
        conn_factory=db.connect,
        state=paths.state_dir(),
        now=lambda: datetime.now(UTC),
        markets=list(boot_.settings.markets),
        gates={},
        stop_event=threading.Event(),
        dry_run=dry_run,
        notifier=make_notifier(
            secrets, enabled=boot_.settings.notify.enabled, record=ledger_recorder(db.connect)
        ),
        secrets=secrets,
        registry=boot_.registry,
        hooks=hooks.by_point(boot_.features),
        ports=boot_.ports,
    )
    for f in boot_.features:
        for g in f.gates:
            deps.gates[g.name] = g.build(boot_, deps)
    return deps


# 웹이 받지 않는 주문 집행 포트 이름(현물 executor 포트). 이름 밖에서도 `execute`·주문 호출을
# 드러내는 포트는 `_executes` 가 거른다 — 웹은 원장 + 큐만 쓴다(M11 Ruling T3-R3).
EXECUTION_PORTS = frozenset({"stock_exec"})


def _executes(port: object) -> bool:
    for name in dir(port):
        if name.startswith("_"):
            continue
        low = name.lower()
        if ("execute" in low or "order" in low) and callable(getattr(port, name, None)):
            return True
    return False


# 웹 라우트가 accounts 포트에서 쓰는 조회 메서드(계좌 목록·일시정지·정책). brokers·fo_brokers
# (주문 경로)·market_client·holiday_fn 은 웹에 넘기지 않는다 — 시세는 quotes 가 따로 만든다
# (M11 최종 리뷰 I2).
READ_ONLY_ACCOUNTS = ("accounts", "fo_accounts", "paused", "policy")


class ReadOnlyAccounts:
    """accounts 포트의 웹용 읽기 전용 보기: `READ_ONLY_ACCOUNTS` 중 원 포트에 있는 것만
    위임한다(없는 메서드는 래퍼에도 없어 라우트의 `getattr(port, "fo_accounts", None)` 분기가
    그대로 산다)."""

    def __init__(self, port: object) -> None:
        for name in READ_ONLY_ACCOUNTS:
            fn = getattr(port, name, None)
            if callable(fn):
                setattr(self, name, fn)


def web_ports(ports: dict) -> dict:
    """부트가 조립한 포트 중 주문 집행 포트를 뺀 사본(웹 프로세스 분리). accounts 포트는
    `ReadOnlyAccounts` 로 감싼다."""
    out = {n: p for n, p in ports.items() if n not in EXECUTION_PORTS and not _executes(p)}
    if "accounts" in out:
        out["accounts"] = ReadOnlyAccounts(out["accounts"])
    return out


def build_web_deps(boot_: Boot) -> WebDeps:
    return WebDeps(
        settings=boot_.settings,
        conn_factory=db.connect,
        state=paths.state_dir(),
        token=load_secrets().WEB_TOKEN,
        dist=paths.web_dist(),
        now=lambda: datetime.now(UTC),
        extra_hosts=tuple(boot_.settings.web.tailscale_hosts),
        tailscale_users=tuple(boot_.settings.web.tailscale_users),
        public_base=boot_.settings.web.public_base,
        features=boot_.features,
        ports=web_ports(boot_.ports),
    )


@config_errors
def daemon(dry_run: bool = typer.Option(False, "--dry-run", help="주문 없이 실행")) -> None:
    """스케줄러 데몬을 시작하고 종료 신호까지 기다린다. 다른 인스턴스가 잠금을 쥐고 있으면
    무거운 조립 전에 안내하고 exit 0(로그온 재기동 반복 방지, FR-1)."""
    running = app_cmd.running_web()
    if running is not None:
        typer.echo(f"이미 실행 중입니다 — http://{running[0]}:{running[1]}")
        return
    other = app_cmd.other_daemon_root()
    if other is not None:
        from trader.kernel.daemon.lock import other_root_message

        typer.echo(other_root_message(other))
        return
    b = boot(ports=True, schedule=True, log_name="daemon")
    d = Daemon(b.features, build_daemon_deps(b, dry_run=dry_run))
    started = False
    try:
        if not d.start():
            typer.echo("다른 데몬이 실행 중입니다", err=True)
            raise typer.Exit(code=1)
        started = True
        # 타임아웃 없는 wait 는 Windows 에서 SIGINT 로 깨지 않는다
        while not d.deps.stop_event.wait(1.0):
            pass
    finally:
        if started:  # 기동 실패는 start 가 스스로 정리한다(teardown 은 돌리지 않는다)
            d.shutdown()
        b.conn.close()


@config_errors
def web(
    host: str | None = typer.Option(None, "--host", help="바인드 주소 (기본: settings.web.bind)"),
    port: int | None = typer.Option(None, "--port", help="포트 (기본: settings.web.port)"),
) -> None:
    """웹 UI·API 서버를 띄운다."""
    import uvicorn

    from trader.kernel.web.app import LOOPBACK_HOSTS, create_app

    ensure_web_token()  # app 과 같은 순서: 토큰 없이 띄우면 제어 API 가 통째로 닫힌다(T19-R1)
    b = boot(ports=True, log_name="web")  # 계좌 env(실전 confirm) 등은 ports 로 본다
    host = host or b.settings.web.bind
    port = port or b.settings.web.port
    if host not in LOOPBACK_HOSTS:
        logger.warning("웹 서버를 루프백 밖(%s)에 바인드합니다 — tailscale serve 를 쓰세요", host)
    try:
        uvicorn.run(create_app(build_web_deps(b)), host=host, port=port, log_config=None)
    finally:
        b.conn.close()


def build_command_ctx(boot_: Boot, *, echo) -> CommandContext:
    return CommandContext(
        conn=boot_.conn,
        settings=boot_.settings,
        state=paths.state_dir(),
        now=lambda: datetime.now(UTC),
        notifier=make_notifier(boot_.secrets, enabled=boot_.settings.notify.enabled),
        features=boot_.features,
        echo=echo,
        ports=boot_.ports,
    )


@config_errors
def notify_test() -> None:
    """텔레그램 테스트 메시지를 1건 보낸다."""
    b = boot(settings=True, secrets=True)
    notifier = make_notifier(b.secrets, enabled=b.settings.notify.enabled)
    stamp = datetime.now(KST).strftime("%H:%M:%S")
    if notifier.send(f"trader 알림 테스트 {stamp}"):
        null = isinstance(notifier, NullNotifier)
        note = " (텔레그램 미설정·꺼짐 — 실제 전송 없음)" if null else ""
        typer.echo("전송 성공" + note)
    else:
        typer.echo("전송 실패", err=True)
        raise typer.Exit(code=1)


telegram_app = typer.Typer(help="텔레그램 수신 (승인 명령 폴링)")


def _telegram_run(*, interval: int | None, max_iterations: int | None) -> None:
    b = boot(ports=True, log_name="telegram")
    try:
        receiver = make_receiver(b.secrets, enabled=b.settings.notify.enabled)
        if receiver is None:
            typer.echo("오류: 텔레그램 토큰·chat_id 가 없습니다", err=True)
            raise typer.Exit(code=1)
        wait = b.settings.notify.poll_interval_s if interval is None else interval
        ctx = build_command_ctx(b, echo=typer.echo)
        n = 0
        while True:
            poll_once(ctx, receiver)
            if isinstance(ctx.notifier, TelegramNotifier):
                ctx.notifier.flush_outbox()
            n += 1
            if max_iterations is not None and n >= max_iterations:
                break
            time.sleep(wait)
    finally:
        b.conn.close()


@config_errors
def telegram_poll() -> None:
    """getUpdates 1회: 텔레그램 명령을 처리하고 오프셋을 저장한다."""
    _telegram_run(interval=0, max_iterations=1)


@config_errors
def telegram_loop(
    interval: int | None = typer.Option(
        None, "--interval", help="폴링 주기(초, 기본: settings.notify.poll_interval_s)"
    ),
    max_iterations: int | None = typer.Option(None, "--max-iterations", hidden=True),
) -> None:
    """텔레그램 명령을 주기적으로 처리한다 (데몬 밖에서 단독 실행용)."""
    _telegram_run(interval=interval, max_iterations=max_iterations)


telegram_app.command("poll")(telegram_poll)
telegram_app.command("loop")(telegram_loop)


def _version_cb(value: bool) -> None:
    if value:
        typer.echo(f"trader {__version__}")
        raise typer.Exit()


def _root(
    version: bool = typer.Option(
        False, "--version", callback=_version_cb, is_eager=True, help="버전을 출력하고 끝낸다"
    ),
) -> None:
    """KIS 자동매매 에이전트"""


def build_app(features: list[Feature]) -> typer.Typer:
    root = TraderApp(help="KIS 자동매매 에이전트", no_args_is_help=True)
    root.callback()(_root)
    root.command("init")(init_cmd.init)
    root.command("selfcheck")(selfcheck.selfcheck)
    root.command("status")(status_cmd.status)
    root.command("watchdog")(watchdog_cmd.watchdog)
    root.command("install")(install_cmd.install)
    root.command("migrate-home")(migrate_cmd.migrate_home)
    root.command("daemon")(daemon)
    root.command("web")(web)
    root.command("app")(app_cmd.app)
    root.command("notify-test")(notify_test)
    root.add_typer(telegram_app, name="telegram")
    root.add_typer(dev_app, name="dev")
    root.add_typer(build_tool_app(features), name="tool")
    root.add_typer(build_jobs_app(features), name="jobs")
    for f in features:
        if f.cli is not None:
            root.add_typer(f.cli, name=f.id)
    return root


def _build_default_app() -> typer.Typer:
    """import 시점에 기능을 발견한다. 실패해도 init·--help 는 돼야 하므로 기능 명령만 포기한다."""
    try:
        feats = discover()
    except Exception as e:  # noqa: BLE001 - 기능 오류가 커널 명령을 막지 않게
        logger.warning("기능 발견 실패, 하위 명령 생략: %s", e)
        feats = []
    return build_app(feats)


app = _build_default_app()
