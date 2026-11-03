"""scalp 상태 파일·사건 기록·판정 키 — `state/scalp/<date>/<market>.json` 과 같은 폴더의
`events.jsonl`. 시각은 호출자가 넘긴다(A12: 안에서 `datetime.now()` 를 부르지 않는다)."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from trader.kernel.platform import paths


def state_dir(state: Path | None, today: date) -> Path:
    """`<state>/scalp/<YYYY-MM-DD>` (state=None 이면 프로젝트 state 폴더)."""
    return (state or paths.state_dir()) / "scalp" / today.isoformat()


def load_state(state: Path | None, today: date, market: str) -> dict:
    p = state_dir(state, today) / f"{market}.json"
    if not p.exists():
        return {"open": [], "closed": [], "done": [], "halted": None}
    return json.loads(p.read_text(encoding="utf-8"))


def save_state(state: Path | None, today: date, market: str, data: dict) -> None:
    """임시 파일에 쓰고 바꿔 넣는다(쓰는 중 죽어도 옛 상태가 남는다)."""
    d = state_dir(state, today)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"{market}.json.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(d / f"{market}.json")


def event(
    state: Path | None, today: date, market: str, kind: str, *, now: datetime, **kw: Any
) -> None:
    """사건 한 줄을 `events.jsonl` 에 덧붙인다(`at` = 주입된 now)."""
    d = state_dir(state, today)
    d.mkdir(parents=True, exist_ok=True)
    row = {"at": now.isoformat(), "market": market, "kind": kind} | kw
    with (d / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def read_events(state: Path | None, today: date) -> list[dict]:
    """그날 사건 목록(파일이 없으면 빈 목록, 깨진 줄은 건너뜀)."""
    ev = state_dir(state, today) / "events.jsonl"
    if not ev.exists():
        return []
    out: list[dict] = []
    for line in ev.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def entry_key(market: str, today: date, ticker: str) -> str:
    return f"entry:{market}:{today.isoformat()}:{ticker}"


def advice_key(market: str, pos: dict) -> str:
    """조언 키는 포지션 단위(시장·종목·진입 시각) — 같은 종목의 다른 날 포지션과 섞이지 않는다."""
    return f"hold:{market}:{pos['ticker']}:{pos.get('entry_at') or pos.get('entry_time')}"


def queue_note(st: dict, **item: Any) -> None:
    """진입·청산 알림은 틱마다 보내지 않고 상태 파일 outbox 에 모은다(알림 피로)."""
    st.setdefault("outbox", []).append(item)
