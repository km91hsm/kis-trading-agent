"""시스템 상태 렌더: `trader status` 의 `state/STATUS.md`(≤ 8,000자)와 텔레그램 `/status` 본문.

커널 절(운영 점검·데몬·마지막 실행·오늘 LLM 호출)은 기능의 `status_sections` 와 같은 모양
(`StatusSection`)이라 한 줄기로 모은다. 커널은 기능 절을 `ctx.features[*].status_sections` 로만
만난다. Markdown 은 커널 4절 + 기능 절 + 구현 진행도 + 다음 할 일, 텔레그램은 커널 앞 2절 + 기능 절.

`ctx` 는 `notify.commands.CommandContext` 이지만 그 모듈이 이 모듈을 가져오므로(순환)
Any 로 받는다."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from trader.kernel.config.time import KST
from trader.kernel.daemon import heartbeat
from trader.kernel.execution.ledger import day_bounds
from trader.kernel.ops import health
from trader.kernel.platform import paths
from trader.kernel.registry.types import StatusSection
from trader.kernel.session.calls import FAKE_MODEL

logger = logging.getLogger("trader.ops")

MAX_CHARS = 8000
OPS_ITEMS = 3  # 운영 점검 절에 보이는 비-ok 항목 수
RECENT_RUNS = 5
_OPEN = re.compile(r"^\s*- \[ \]\s*(.*)$")
_DONE = re.compile(r"^\s*- \[[xX]\]")


# --- TODO 체크리스트 ----------------------------------------------------------------------


def todo_progress(todo_path: Path) -> dict[str, tuple[int, int]]:
    """`## ...` 절 제목 → (완료, 전체) 체크박스 수. 파일이 없으면 빈 dict."""
    if not todo_path.exists():
        return {}
    result: dict[str, tuple[int, int]] = {}
    current: str | None = None
    for line in todo_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            result[current] = (0, 0)
        elif current is not None:
            done, total = result[current]
            if _DONE.match(line):
                result[current] = (done + 1, total + 1)
            elif _OPEN.match(line):
                result[current] = (done, total + 1)
    return result


def next_todos(todo_path: Path, limit: int = 5) -> list[str]:
    """열린 체크박스(`- [ ]`) 앞에서부터 limit 개."""
    if not todo_path.exists():
        return []
    items: list[str] = []
    for line in todo_path.read_text(encoding="utf-8").splitlines():
        m = _OPEN.match(line)
        if m:
            items.append(m.group(1).strip())
            if len(items) >= limit:
                break
    return items


# --- 커널 절 ------------------------------------------------------------------------------


def _now(ctx: Any) -> datetime:
    return ctx.now()


def ops_lines(ctx: Any) -> list[str]:
    """데몬이 마지막으로 적은 점검 결과 머리줄 + 비-ok 항목 앞 3개."""
    result = health.latest(ctx.conn)
    items = (result or {}).get("items") or []
    return [health.headline(result), *(f"- {i.get('text', '')}" for i in items[:OPS_ITEMS])]


def daemon_lines(ctx: Any) -> list[str]:
    st = heartbeat.read_status(ctx.state or paths.state_dir(), _now(ctx), [])
    if not st["alive"]:
        return ["데몬: 꺼짐"]
    beat = "박동 기록 없음" if st["age_s"] is None else f"박동 {st['age_s']}초 전"
    stale = " (멈춤 의심)" if st["stale"] else ""
    return [f"데몬: 실행 중 pid {st['pid']} · {beat}{stale}"]


def _kst(iso: str | None) -> str:
    if not iso:
        return "-"
    try:
        return datetime.fromisoformat(iso).astimezone(KST).strftime("%m-%d %H:%M")
    except (ValueError, TypeError):
        return str(iso)


def runs_lines(ctx: Any) -> list[str]:
    rows = ctx.conn.execute(
        "SELECT kind, market, started_at, status FROM runs ORDER BY id DESC LIMIT ?",
        (RECENT_RUNS,),
    ).fetchall()
    if not rows:
        return ["아직 실행 기록 없음"]
    return [
        f"- {r['kind']} {r['market'] or '-'} {_kst(r['started_at'])} → {r['status']}" for r in rows
    ]


def _dec(v: Any) -> Decimal:
    try:
        return Decimal(str(v)) if v not in (None, "") else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


def calls_lines(ctx: Any) -> list[str]:
    """오늘(KST) LLM 호출 건수·비용(fake 제외). cost_usd 는 TEXT 라 Decimal 로 합산."""
    start, _ = day_bounds(_now(ctx).astimezone(KST).date(), None)
    rows = ctx.conn.execute(
        "SELECT cost_usd FROM agent_calls WHERE created_at >= ? AND COALESCE(model, '') != ?",
        (start, FAKE_MODEL),
    ).fetchall()
    cost = sum((_dec(r["cost_usd"]) for r in rows), Decimal(0))
    return [f"- {len(rows)}건, ${cost:.2f}"]


KERNEL_SECTIONS: tuple[StatusSection, ...] = (
    StatusSection("운영 점검", ops_lines),
    StatusSection("데몬", daemon_lines),
    StatusSection("마지막 실행", runs_lines),
    StatusSection("오늘 LLM 호출", calls_lines),
)
TELEGRAM_KERNEL_SECTIONS = KERNEL_SECTIONS[:2]


# --- 모으기·렌더 ---------------------------------------------------------------------------


def _collect(ctx: Any, kernel: Sequence[StatusSection]) -> list[tuple[str, list[str], bool]]:
    """(제목, 줄, 실패 여부). 절 하나의 실패가 나머지 절을 가리지 않는다."""
    out: list[tuple[str, list[str], bool]] = []
    for sec in (*kernel, *(s for f in ctx.features for s in f.status_sections)):
        try:
            out.append((sec.title, [str(x) for x in sec.render(ctx) or []], False))
        except Exception as e:  # noqa: BLE001 - 절 실패 격리
            logger.exception("상태 절 조회 실패: %s", sec.title)
            out.append((sec.title, [f"조회 실패({type(e).__name__}: {e})"], True))
    return out


def sections(ctx: Any, *, kernel: Sequence[StatusSection]) -> list[tuple[str, list[str]]]:
    """커널 절(kernel) + 기능 절. 실패한 절은 `["조회 실패(ExcName: msg)"]`."""
    return [(title, lines) for title, lines, _ in _collect(ctx, kernel)]


def telegram_lines(ctx: Any) -> list[str]:
    """커널 앞 2절 + 기능 절의 줄. 비어 있지 않은 절마다 `[제목]` 한 줄을 앞세운다(T7-R4 —
    대체 문구·줄이 어느 절 것인지 보이게). 빈 절은 생략하고, 실패한 절은 `[제목]` 뒤에
    `- 제목: 조회 실패(…)` 한 줄."""
    out: list[str] = []
    for title, lines, failed in _collect(ctx, TELEGRAM_KERNEL_SECTIONS):
        if lines:
            out.append(f"[{title}]")
            out.extend([f"- {title}: {lines[0]}"] if failed else lines)
    return out


def render_markdown(ctx: Any, *, todo_path: Path | None) -> str:
    stamp = _now(ctx).astimezone(KST).strftime("%Y-%m-%d %H:%M KST")
    lines = [f"# 시스템 상태 (생성: {stamp})"]
    for title, body in sections(ctx, kernel=KERNEL_SECTIONS):
        if body:
            lines += ["", f"## {title}", *body]
    if todo_path is not None:
        lines += ["", "## 구현 진행도"]
        lines += [f"- {t}: {d}/{n}" for t, (d, n) in todo_progress(todo_path).items()]
        lines += ["", "## 다음 할 일"]
        lines += [f"- {t}" for t in next_todos(todo_path)]
    text = "\n".join(lines)
    if len(text) >= MAX_CHARS:
        text = text[: MAX_CHARS - 1]
    return text if text.endswith("\n") else text + "\n"


def write_status(text: str, path: Path | None = None) -> Path:
    """기본 `state/STATUS.md` 에 utf-8 로 쓴다."""
    path = path or paths.state_dir() / "STATUS.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
