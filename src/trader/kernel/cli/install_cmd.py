"""`trader install`: 로그온 시 데몬(또는 watchdog) 자동 시작 등록 — 결과 JSON 한 줄."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import typer

from trader.kernel.cli.boot import boot, config_errors
from trader.kernel.config import ConfigError
from trader.kernel.platform import installer
from trader.kernel.platform.claude_bin import find_claude

logger = logging.getLogger("trader.cli")


@config_errors
def install(
    dry_run: bool = typer.Option(False, "--dry-run", help="파일을 쓰지 않고 내용·명령만 출력"),
    uninstall: bool = typer.Option(False, "--uninstall", help="등록 해제"),
    watchdog: bool = typer.Option(
        False, "--watchdog", help="데몬 대신 외부 감시(trader watchdog, 5분 간격)를 등록·해제"
    ),
) -> None:
    """로그온 시 데몬 자동 시작 등록(macOS launchd / Windows 작업 스케줄러).
    --watchdog 이면 데몬 밖 감시를 5분 간격 1회 실행으로 등록한다."""
    kind = "watchdog" if watchdog else "daemon"
    if uninstall:
        r = installer.uninstall(kind=kind)
    else:
        settings = boot(settings=True).settings
        extra: list[Path] = []
        try:  # launchd 는 셸 PATH 가 없다 — claude 디렉터리를 PATH 앞에 둔다
            extra.append(find_claude(settings.claude_bin).parent)
        except ConfigError as e:
            logger.warning("claude 실행파일을 찾지 못해 PATH 에 넣지 않음: %s", e)
        r = installer.install(
            dry_run=dry_run,
            kind=kind,
            extra_paths=extra,
            # 종료 시 데몬이 실행 중인 세션을 기다리므로 launchd SIGKILL 을 그보다 늦춘다
            exit_timeout_s=int(settings.llm.timeout_s) + 60,
        )
    typer.echo(json.dumps({**r, "path": str(r["path"]), "kind": kind}, ensure_ascii=False))
