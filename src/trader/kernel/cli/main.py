"""`trader` 루트 typer 앱: 커널 명령과 기능이 노출한 하위 명령을 묶는다."""

from __future__ import annotations

import logging
import os
import sys
import threading
from datetime import UTC, datetime

import typer
import uvicorn

from trader.kernel.cli import init_cmd, selfcheck
from trader.kernel.cli.boot import Boot, boot, config_errors
from trader.kernel.cli.dev import dev_app
from trader.kernel.daemon.runner import Daemon, DaemonDeps
from trader.kernel.ledger import db
from trader.kernel.platform import paths
from trader.kernel.registry import discover
from trader.kernel.registry.types import Feature
from trader.kernel.web.app import create_app
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
    return DaemonDeps(
        settings=boot_.settings,
        schedule=boot_.schedule,
        conn_factory=db.connect,
        state=paths.state_dir(),
        now=lambda: datetime.now(UTC),
        markets=list(boot_.settings.markets),
        gates={},
        stop_event=threading.Event(),
        dry_run=dry_run,
    )


def build_web_deps(boot_: Boot) -> WebDeps:
    return WebDeps(
        settings=boot_.settings,
        conn_factory=db.connect,
        state=paths.state_dir(),
        token=os.environ.get("WEB_TOKEN"),
        dist=paths.web_dist(),
        now=lambda: datetime.now(UTC),
        features=boot_.features,
    )


@config_errors
def daemon(dry_run: bool = typer.Option(False, "--dry-run", help="주문 없이 실행")) -> None:
    """스케줄러 데몬을 시작하고 종료 신호까지 기다린다."""
    b = boot(settings=True, schedule=True, conn=True, log_name="daemon")
    d = Daemon(b.features, build_daemon_deps(b, dry_run=dry_run))
    try:
        if not d.start():
            typer.echo("다른 데몬이 실행 중입니다", err=True)
            raise typer.Exit(code=1)
    except BaseException:
        b.conn.close()
        raise
    try:
        # 타임아웃 없는 wait 는 Windows 에서 SIGINT 로 깨지 않는다
        while not d.deps.stop_event.wait(1.0):
            pass
    finally:
        d.shutdown()
        b.conn.close()


@config_errors
def web(
    host: str | None = typer.Option(None, "--host", help="바인드 주소 (기본: settings.web.bind)"),
    port: int | None = typer.Option(None, "--port", help="포트 (기본: settings.web.port)"),
) -> None:
    """웹 UI·API 서버를 띄운다."""
    b = boot(settings=True, conn=True, log_name="web")
    host = host or b.settings.web.bind
    port = port or b.settings.web.port
    try:
        uvicorn.run(create_app(build_web_deps(b)), host=host, port=port, log_config=None)
    finally:
        b.conn.close()


def build_app(features: list[Feature]) -> typer.Typer:
    root = TraderApp(help="KIS 자동매매 에이전트", no_args_is_help=True)
    root.command("init")(init_cmd.init)
    root.command("selfcheck")(selfcheck.selfcheck)
    root.command("daemon")(daemon)
    root.command("web")(web)
    root.add_typer(dev_app, name="dev")
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
