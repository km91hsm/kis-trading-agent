"""`trader jobs list|run|kinds`: 잡 큐 조회·요청. 실행은 데몬 큐 틱만 한다(요청만 넣는다)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import typer

from trader.kernel.cli.boot import boot, config_errors
from trader.kernel.config import ConfigError
from trader.kernel.daemon import heartbeat
from trader.kernel.daemon import queue as jobq
from trader.kernel.daemon.lock import Lock
from trader.kernel.platform import paths
from trader.kernel.registry.types import Feature


def parse_params(items: list[str] | None) -> dict:
    """`k=v` 목록 → dict. 값은 JSON 으로 읽히면 그 값(false·3·null), 아니면 문자열."""
    out: dict = {}
    for item in items or []:
        key, sep, raw = item.partition("=")
        if not sep or not key.strip():
            raise ConfigError(f"--param 은 k=v 형식이어야 합니다: {item!r}")
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
        out[key.strip()] = value
    return out


def daemon_running() -> bool:
    """데몬 잠금(pid 파일)을 살아 있는 프로세스가 쥐고 있는가."""
    return Lock(paths.state_dir() / heartbeat.LOCK_NAME).held()


def build_jobs_app(features: list[Feature]) -> typer.Typer:
    """기능들이 선언한 QueueKind 로 `jobs` 하위 명령을 만든다."""
    app = typer.Typer(help="작업 큐 (요청만 넣고 실행은 데몬 큐 틱)", no_args_is_help=True)

    def kinds() -> jobq.Kinds:
        return jobq.kinds_of(features)

    @app.command("list")
    @config_errors
    def jobs_list(limit: int = typer.Option(20, "--limit", help="표시 건수")) -> None:
        """대기·실행 중 요청(실행 순서)과 최근 종료분을 보여준다."""
        b = boot(conn=True)
        try:
            rows = jobq.list_recent(b.conn, limit=limit)
        finally:
            b.conn.close()
        if not rows:
            typer.echo("요청 없음")
            return
        table = kinds()
        for r in rows:
            extra = r.get("progress") if r["status"] == "running" else r.get("error")
            params = jobq.canonical(r["params"]) if r["params"] else ""
            typer.echo(
                f"#{r['id']} {r['status']:7} p{r.get('priority', 0)} "
                f"{jobq.label(table, r['kind'])} {params}".rstrip()
                + f" ({r['source']}, {r['requested_at']})"
                + (f" — {extra}" if extra else "")
            )

    @app.command("run")
    @config_errors
    def jobs_run(
        kind: str = typer.Argument(..., help="작업 종류 (`trader jobs kinds`)"),
        param: list[str] | None = typer.Option(  # noqa: B008 - typer 반복 옵션
            None, "--param", help="파라미터 k=v (반복 가능)"
        ),
        priority: int | None = typer.Option(None, "--priority", help="우선순위(기본 kind 별)"),
    ) -> None:
        """요청을 큐에 넣는다. 데몬이 실행 중이어야 한다(꺼져 있으면 거부)."""
        if not daemon_running():
            typer.echo("데몬이 실행 중이어야 합니다 (`trader daemon`)", err=True)
            raise typer.Exit(code=1)
        table = kinds()
        params = parse_params(param)
        b = boot(conn=True)
        try:
            rid = jobq.enqueue(
                b.conn, table, kind, params, source="cli", now=datetime.now(UTC),
                priority=priority,
            )  # fmt: skip
        except ValueError as e:  # 알 수 없는 kind·params 오류·중복(DuplicateRequest)
            typer.echo(f"오류: {e}", err=True)
            raise typer.Exit(code=1) from e
        finally:
            b.conn.close()
        typer.echo(f"요청 #{rid} {jobq.label(table, kind)} — 데몬이 순서대로 실행합니다")

    @app.command("kinds")
    def jobs_kinds() -> None:
        """요청 가능한 작업 종류(kind·레인·이름)."""
        for k, q in kinds().items():
            typer.echo(f"{k:18} {q.lane:6} {q.label}")

    return app
