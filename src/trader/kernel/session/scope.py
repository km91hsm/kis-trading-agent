"""세션 범위: 러너가 띄운 LLM 세션이 무엇을 해도 되는지 도구 프로세스에 전한다.

LLM 은 Bash 로 `trader tool …` 만 부를 수 있지만, 그 안에서는 어떤 도구 명령이든 고를 수 있다.
수집한 뉴스 본문이 지시를 흉내 내면(프롬프트 인젝션) 종목 분석 세션이 시장 판단을 제출하거나
배정받지 않은 종목에 결정을 낼 수 있다. 러너는 세션을 띄울 때 그 세션의 종류·허용 도구·dry-run·
배정 종목을 `state/sessions/<session_id>.json` 에 적고 env `TRADER_SESSION_ID` 로 가리킨다.
도구 프로세스는 env 가 아니라 **이 파일**을 믿는다 — LLM 은 자기 세션의 파일을 고칠 수 없다.

- env 에 id 가 없다: 러너 밖(사람·테스트)의 호출. 도구 제한은 없지만 주문은 항상 dry-run.
- id 는 있는데 파일이 없다: 끝난 세션이거나 지어낸 id. 모든 도구를 거부한다.
- 실제 주문(dry-run 아님)은 `TRADER_DRY_RUN=0` **이고** 범위 파일이 dry-run 이 아닐 때만.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from trader.kernel.platform import paths

logger = logging.getLogger("trader.session")

ENV_ID = "TRADER_SESSION_ID"
ENV_DRY_RUN = "TRADER_DRY_RUN"
DIR_NAME = "sessions"
_ID_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")  # uuid 모양만 — 경로 조작 차단

UNKNOWN = {"unknown": True}  # id 는 왔지만 범위 파일이 없다(끝난 세션·지어낸 id)


def _path(state: Path, session_id: str) -> Path:
    return state / DIR_NAME / f"{session_id}.json"


def open_scope(
    state: Path,
    session_id: str,
    *,
    kind: str,
    tools: Iterable[str] | None,
    dry_run: bool,
    market: str | None,
    tickers: Iterable[str],
    date: str | None = None,
) -> Path:
    """세션 시작 전에 범위 파일을 쓴다. tools 가 None 이면 도구 제한 없음(dry-run·종목 범위만),
    빈 목록이면 그 세션은 어떤 도구도 부를 수 없다."""
    if not _ID_RE.match(session_id):
        raise ValueError(f"세션 id 형식이 아닙니다: {session_id!r}")
    path = _path(state, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "session_id": session_id,
        "kind": kind,
        "tools": None if tools is None else sorted(set(tools)),
        "dry_run": bool(dry_run),
        "market": market,
        "date": date,  # 세션 운영일(ISO) — 다른 날짜 폴더에 쓰지 못하게
        "tickers": sorted({str(t) for t in tickers}),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return path


def close_scope(state: Path, session_id: str) -> None:
    """세션이 끝나면 지운다(없어도 조용히) — 끝난 세션의 id 로는 도구를 부를 수 없게."""
    try:
        _path(state, session_id).unlink(missing_ok=True)
    except OSError as e:
        logger.warning("세션 범위 파일 삭제 실패 %s: %s", session_id, e)


def current(state: Path | None = None) -> dict[str, Any] | None:
    """이 프로세스가 속한 세션의 범위. 러너 밖이면 None, id 는 있는데 파일이 없거나 읽을 수
    없으면 `UNKNOWN`."""
    session_id = os.environ.get(ENV_ID, "").strip()
    if not session_id:
        return None
    if not _ID_RE.match(session_id):
        return dict(UNKNOWN)
    try:
        data = json.loads(_path(state or paths.state_dir(), session_id).read_text("utf-8"))
    except (OSError, ValueError):
        return dict(UNKNOWN)
    if isinstance(data, dict) and data.get("session_id") == session_id:
        return data
    return dict(UNKNOWN)


def tool_error(name: str | None, scope: dict[str, Any] | None) -> str | None:
    """이 세션이 도구 `name`(최상위 명령 이름)을 부를 수 없으면 사유, 부를 수 있으면 None."""
    if scope is None:
        return None
    if scope.get("unknown"):
        return "세션 범위를 확인할 수 없어 도구를 실행하지 않습니다(끝난 세션이거나 잘못된 세션 id)"
    allowed = scope.get("tools")
    if allowed is not None and name not in allowed:
        return f"{scope.get('kind')} 세션은 `{name}` 도구를 쓸 수 없습니다"
    return None


def dry_run(state: Path | None = None) -> bool:
    """도구 프로세스의 dry-run 여부. 실제 주문은 env 가 "0" 이고 **러너가 적은 범위 파일**도
    dry-run 이 아닐 때만 — env 만으로는(사람의 직접 호출·LLM 의 env 덮어쓰기) 주문이 나가지
    않는다."""
    if os.environ.get(ENV_DRY_RUN, "1") != "0":
        return True
    scope = current(state)
    return scope is None or bool(scope.get("unknown")) or bool(scope.get("dry_run", True))


def ticker_error(ticker: str, scope: dict[str, Any] | None) -> str | None:
    """배정 종목이 있는 세션이 다른 종목을 다루려 하면 사유. 범위가 없거나(러너 밖) 종목을
    배정하지 않는 세션이면 None. 범위를 확인할 수 없으면 거부한다."""
    if scope is None:
        return None
    if scope.get("unknown"):
        return "세션 범위를 확인할 수 없어 도구를 실행하지 않습니다"
    assigned = scope.get("tickers") or []
    if assigned and ticker not in assigned:
        return f"이 세션에 배정되지 않은 종목입니다: {ticker} (배정: {', '.join(assigned)})"
    return None


def args_error(args: dict[str, Any], scope: dict[str, Any] | None) -> str | None:
    """도구 인자(`ticker`·`market`·`date`)가 세션 범위 밖이면 사유. 도구 실행 헬퍼가 본문을 돌리기
    전에 부른다(`cli/tool_cmd.run_tool(args=)`) — 제출 도구뿐 아니라 체크포인트 쓰기·수집에도
    걸려, 종목 A 세션이 종목 B 의 리포트를 쓰거나 KR 세션이 US 시장 판단을 낼 수 없다."""
    if scope is None:
        return None
    if scope.get("unknown"):
        return "세션 범위를 확인할 수 없어 도구를 실행하지 않습니다"
    ticker = args.get("ticker")
    if isinstance(ticker, str) and (err := ticker_error(ticker, scope)):
        return err
    market = args.get("market")
    if isinstance(market, str) and scope.get("market") and market != scope["market"]:
        return f"이 세션의 시장({scope['market']})이 아닙니다: {market}"
    day = args.get("date")
    day = day.isoformat() if hasattr(day, "isoformat") else day
    if isinstance(day, str) and scope.get("date") and day != scope["date"]:
        return f"이 세션의 운영일({scope['date']})이 아닙니다: {day}"
    return None


STALE_AFTER_S = 86400  # 이보다 오래된 범위 파일은 끝난 세션의 것이다(가장 긴 세션도 수 시간)


def purge_stale(state: Path, *, older_than_s: float = STALE_AFTER_S) -> int:
    """오래된 범위 파일을 지운다(데몬 기동 정리 — 강제 종료로 남은 파일). 지운 개수."""
    folder = state / DIR_NAME
    if not folder.is_dir():
        return 0
    cutoff = time.time() - older_than_s
    removed = 0
    for path in folder.glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed
