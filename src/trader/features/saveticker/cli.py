"""SaveTicker 전용 CLI. 수집은 Chrome 확장이 하고, 이 CLI 는 조회·확장 준비·등록만 한다.
브로커·LLM·거래 데몬을 실행하지 않는다."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from trader.features.saveticker.models import Capture
from trader.features.saveticker.paths import home
from trader.features.saveticker.store import Store

app = typer.Typer(help="SaveTicker Chrome 확장 수집·파일 조회", no_args_is_help=True)


def root() -> Path:
    return home() / "data"


def runtime() -> Path:
    return home() / "runtime"


def _print(value):
    typer.echo(json.dumps(value, ensure_ascii=False))


@app.command("status")
def status_cmd():
    """저장 상태와 실행 프로세스의 생존·최신 수집 시각을 표시한다."""
    from trader.kernel.platform.procs import pid_alive

    folder = root()
    p = folder / "status.json"
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"streams": {}}
    runner = runtime() / "runner.json"
    if runner.exists():
        running = json.loads(runner.read_text(encoding="utf-8"))
        running["process_alive"] = pid_alive(running.get("pid", 0))
        data["runner"] = running
    now = datetime.now(UTC)
    for stream in data["streams"].values():
        last = stream.get("last_success_at")
        stream["age_s"] = (now - datetime.fromisoformat(last)).total_seconds() if last else None
    _print(data)


@app.command("import-capture")
def import_cmd(path: Path):
    """브라우저 캡처 JSON을 검증해 저장한다(원문 오류 값은 출력하지 않음)."""
    try:
        capture = Capture.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        typer.echo("수집 JSON 형식 또는 파일을 확인하세요", err=True)
        raise typer.Exit(1) from None
    _print(Store(root()).save(capture))


@app.command("query")
def query_cmd(
    asof: str | None = typer.Option(None, help="시간대가 포함된 ISO 시각"),
    ticker: str | None = typer.Option(None),
    limit: int = typer.Option(100, min=1, max=10000),
):
    """asof 이전에 실제 수신한 버전만 반환한다(미래 일정은 예정 정보로 유지)."""
    try:
        when = datetime.fromisoformat(asof) if asof else datetime.now(UTC)
        if when.tzinfo is None:
            raise ValueError
    except ValueError:
        typer.echo("asof에는 시간대가 포함된 ISO 시각이 필요합니다", err=True)
        raise typer.Exit(1) from None
    if not (root() / "feed.sqlite3").exists():
        _print([])
        return
    _print(Store(root()).query(asof=when, ticker=ticker, limit=limit))


@app.command("export")
def export_cmd():
    """SQLite에서 JSONL·최신 파일·상태를 재생성한다."""
    Store(root()).export()
    _print({"ok": True, "root": str(root())})


extension_app = typer.Typer(help="일반 Chrome 상시 수집 확장 준비·등록", no_args_is_help=True)
app.add_typer(extension_app, name="extension")


def _bridge_action(function, *args, **kwargs):
    from trader.features.saveticker.bridge_install import InstallError

    try:
        _print(function(*args, **kwargs))
    except InstallError as exc:
        _print({"ok": False, "error": str(exc)})
        raise typer.Exit(1) from None


@extension_app.command("prepare")
def extension_prepare(destination: Annotated[Path, typer.Option("--destination")]):
    """설치·활성화 없이 검토할 확장 패키지와 권한 목록을 만든다."""
    from trader.features.saveticker.bridge_install import prepare

    _bridge_action(prepare, destination)


@extension_app.command("install-host")
def extension_install(package: Path):
    """사용자 승인 후 Native host를 등록한다."""
    from trader.features.saveticker.bridge_install import install_host

    _bridge_action(install_host, package)


@extension_app.command("uninstall-host")
def extension_uninstall(package: Path):
    """확장 중지 후 Native host 등록을 지운다."""
    from trader.features.saveticker.bridge_install import uninstall_host

    _bridge_action(uninstall_host, package)


@extension_app.command("status")
def extension_status():
    """확장 등록 여부·Native host 연결 상태·작업별 상태를 표시한다."""
    from trader.features.saveticker.automation import status

    _print(status(home()))


@app.command("native-host", hidden=True)
def native_host_cmd(origin: str):
    from trader.features.saveticker.bridge_install import native_entry

    code = native_entry(origin)
    if code:
        raise typer.Exit(code)
