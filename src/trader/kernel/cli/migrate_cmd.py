"""`trader migrate-home`: 옛 휴대용 폴더(또는 소스 폴더)의 데이터를 현재 데이터 루트로 복사한다.

- 대상은 `paths.project_root()`(번들은 OS 사용자 데이터 폴더). 원본은 절대 지우지 않는다.
- `config/ secrets/ state/ docs/` 를 복사한다. `*.db` 는 `sqlite3` backup API 로(WAL 안전),
  `-wal/-shm/-journal`·잠금(`*.lock`·`*.lock.os`)·`daemon_heartbeat.json`·`*.tmp` 은 제외한다.
- 거부: 대상에 `state/trader.db` 가 이미 있음, 원본·대상 데몬 잠금이 잡혀 있음, 전역 데몬 잠금이
  원본·대상 소유.
- secrets 는 내용을 출력하지 않고 복사만 하며 권한을 폴더 0700·파일 0600 으로 맞춘다.
- `--saveticker-from` 은 세이브티커 수집 폴더의 `data/` 와 `runtime/bridge-config.json` 을 대상
  `state/external/saveticker/` 로 가져오고, `config/sources.yaml` 의 `saveticker.dir` 이 절대경로면
  기본값으로 되돌린다(그 줄만 치환해 주석 보존).
- 결과는 JSON 1줄(dry-run 이면 계획만)."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import typer

from trader.kernel.daemon import heartbeat
from trader.kernel.daemon.lock import Lock, global_owner
from trader.kernel.platform import paths

DATA_DIRS = ("config", "secrets", "state", "docs")
DB_NAME = "trader.db"
SAVETICKER_DIR = "state/external/saveticker"
SKIP_SUFFIXES = ("-wal", "-shm", "-journal", ".lock", ".lock.os", ".tmp")
SKIP_NAMES = frozenset({heartbeat.FILE_NAME})


class MigrateError(RuntimeError):
    """이전을 거부하는 조건(사용자 안내 문구)."""


@dataclass(frozen=True)
class Item:
    src: Path
    rel: str  # 대상 루트 기준 posix 상대경로
    size: int
    method: str  # "copy" | "sqlite-backup"


def skipped(path: Path) -> bool:
    """런타임 잠금·하트비트·SQLite 부속 파일은 복사하지 않는다."""
    name = path.name
    return name in SKIP_NAMES or name.endswith(SKIP_SUFFIXES)


def _files(base: Path) -> list[Path]:
    if not base.is_dir():
        return []
    return sorted(p for p in base.rglob("*") if p.is_file() and not p.is_symlink())


def _item(src: Path, rel: str) -> Item:
    method = "sqlite-backup" if src.suffix == ".db" else "copy"
    return Item(src=src, rel=rel, size=src.stat().st_size, method=method)


def plan(src_root: Path, saveticker_from: Path | None = None) -> tuple[list[Item], list[str]]:
    """복사할 항목과 제외한 파일(상대경로)."""
    items: list[Item] = []
    skips: list[str] = []
    for d in DATA_DIRS:
        for f in _files(src_root / d):
            rel = f.relative_to(src_root).as_posix()
            (skips.append(rel) if skipped(f) else items.append(_item(f, rel)))
    if saveticker_from is not None:
        sources = [*_files(saveticker_from / "data")]
        bridge = saveticker_from / "runtime" / "bridge-config.json"
        if bridge.is_file():
            sources.append(bridge)
        for f in sources:
            rel = f"{SAVETICKER_DIR}/{f.relative_to(saveticker_from).as_posix()}"
            (skips.append(rel) if skipped(f) else items.append(_item(f, rel)))
    return items, skips


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return a == b


def check(src_root: Path, dst_root: Path, saveticker_from: Path | None) -> None:
    """거부 조건을 검사한다. 문제가 있으면 MigrateError."""
    if not src_root.is_dir():
        raise MigrateError(f"옛 폴더가 없습니다: {src_root}")
    if _same(src_root, dst_root):
        raise MigrateError(f"옛 폴더와 현재 데이터 루트가 같습니다: {dst_root}")
    if saveticker_from is not None and not saveticker_from.is_dir():
        raise MigrateError(f"세이브티커 수집 폴더가 없습니다: {saveticker_from}")
    if (dst_root / "state" / DB_NAME).exists():
        raise MigrateError(
            f"대상에 이미 원장이 있습니다: {dst_root / 'state' / DB_NAME} — 덮어쓰지 않습니다"
        )
    for root, what in ((src_root, "옛 폴더"), (dst_root, "현재 데이터 루트")):
        if Lock(root / "state" / heartbeat.LOCK_NAME).held():
            raise MigrateError(f"{what}({root})의 데몬이 실행 중입니다 — 데몬을 먼저 종료하세요")
    owner = global_owner()
    if owner and any(_same(Path(owner), r) for r in (src_root, dst_root)):
        raise MigrateError(f"데몬({owner})이 실행 중입니다 — 데몬을 먼저 종료하세요")


def _backup_sqlite(src: Path, dst: Path) -> None:
    """WAL 에 남은 쓰기까지 포함한 일관된 사본을 임시 파일에 만든 뒤 이름을 바꾼다."""
    tmp = dst.with_name(dst.name + ".migrating")
    tmp.unlink(missing_ok=True)
    source = sqlite3.connect(str(src), timeout=30)
    try:
        target = sqlite3.connect(str(tmp), timeout=30)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    os.replace(tmp, dst)


def _secure_secrets(dst_root: Path) -> None:
    """secrets/ 폴더 0700·파일 0600 (Windows 는 chmod 가 읽기 전용 비트만 다뤄 건너뛴다)."""
    if sys.platform == "win32":
        return
    base = dst_root / "secrets"
    if not base.is_dir():
        return
    for p in [base, *base.rglob("*")]:
        try:
            os.chmod(p, 0o700 if p.is_dir() else 0o600)
        except OSError:
            pass


def execute(items: list[Item], dst_root: Path) -> None:
    for it in items:
        dst = dst_root / it.rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if it.method == "sqlite-backup":
            _backup_sqlite(it.src, dst)
        else:
            shutil.copy2(it.src, dst)
    _secure_secrets(dst_root)


_SAVETICKER_DIR_LINE = re.compile(
    r"^(?P<indent>[ \t]+)dir:[ \t]*(?P<value>[^#\n]*?)[ \t]*(?P<rest>#.*)?$"
)


def reset_saveticker_dir(sources_yaml: Path, *, dry_run: bool) -> str | None:
    """`saveticker:` 절의 `dir:` 이 절대경로면 기본값으로 되돌린다(그 줄만 치환, 주석 보존).
    바꿨으면(dry-run 이면 바꿀 예정이면) 이전 값을, 아니면 None."""
    if not sources_yaml.is_file():
        return None
    lines = sources_yaml.read_text(encoding="utf-8").splitlines(keepends=True)
    in_section = False
    for i, line in enumerate(lines):
        if re.match(r"^saveticker:\s*(#.*)?$", line.rstrip("\r\n")):
            in_section = True
            continue
        if in_section and line.strip() and not line[0].isspace():
            break  # 다음 최상위 키
        if not in_section:
            continue
        m = _SAVETICKER_DIR_LINE.match(line.rstrip("\r\n"))
        if m is None:
            continue
        value = m.group("value").strip().strip("'\"")
        if not value or not _is_absolute(value):
            return None
        if not dry_run:
            eol = line[len(line.rstrip("\r\n")) :]
            rest = f"  {m.group('rest')}" if m.group("rest") else ""
            lines[i] = f"{m.group('indent')}dir: {SAVETICKER_DIR}{rest}{eol}"
            sources_yaml.write_text("".join(lines), encoding="utf-8")
        return value
    return None


def _is_absolute(value: str) -> bool:
    """POSIX·Windows 절대경로 모두(설정은 다른 OS 에서 만들어졌을 수 있다)."""
    from pathlib import PurePosixPath, PureWindowsPath

    v = os.path.expanduser(value)
    return PurePosixPath(v).is_absolute() or PureWindowsPath(v).is_absolute()


def summarize(items: list[Item]) -> dict[str, dict[str, int]]:
    """항목별(최상위 폴더, 세이브티커는 그 폴더 하나) 파일 수·바이트."""
    out: dict[str, dict[str, int]] = {}
    for it in items:
        key = SAVETICKER_DIR if it.rel.startswith(SAVETICKER_DIR + "/") else it.rel.split("/")[0]
        slot = out.setdefault(key, {"files": 0, "bytes": 0})
        slot["files"] += 1
        slot["bytes"] += it.size
    return out


def migrate(
    src_root: Path, dst_root: Path, *, saveticker_from: Path | None = None, dry_run: bool = False
) -> dict:
    check(src_root, dst_root, saveticker_from)
    items, skips = plan(src_root, saveticker_from)
    if not dry_run:
        execute(items, dst_root)
    reset = None
    if saveticker_from is not None:
        yaml = dst_root / "config" / "sources.yaml"
        if dry_run:  # 복사 뒤의 대상 파일 = 복사될 원본
            yaml = next((i.src for i in items if i.rel == "config/sources.yaml"), yaml)
        reset = reset_saveticker_dir(yaml, dry_run=dry_run)
    return {
        "ok": True,
        "dry_run": dry_run,
        "from": str(src_root),
        "to": str(dst_root),
        "copied": summarize(items),
        "databases": [i.rel for i in items if i.method == "sqlite-backup"],
        "files": len(items),
        "bytes": sum(i.size for i in items),
        "skipped": skips,
        "saveticker_from": str(saveticker_from) if saveticker_from is not None else None,
        "saveticker_dir_reset": reset,
    }


def migrate_home(
    from_: Path = typer.Option(..., "--from", help="옛 휴대용 폴더(또는 소스 폴더)"),  # noqa: B008
    saveticker_from: Path | None = typer.Option(  # noqa: B008 - typer 경로 옵션
        None, "--saveticker-from", help="세이브티커 수집 폴더(data/·runtime/bridge-config.json)"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="복사하지 않고 계획만 출력"),
) -> None:
    """옛 폴더의 config/ secrets/ state/ docs/ 를 현재 데이터 루트로 복사한다(원본 유지)."""
    src = from_.expanduser().resolve()
    st = saveticker_from.expanduser().resolve() if saveticker_from is not None else None
    try:
        out = migrate(src, paths.project_root(), saveticker_from=st, dry_run=dry_run)
    except (MigrateError, OSError, sqlite3.Error) as e:
        typer.echo(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        raise typer.Exit(code=1) from None
    typer.echo(json.dumps(out, ensure_ascii=False))
