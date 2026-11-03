"""단발 LLM 호출: 서브에이전트·도구 없이 `claude -p` 한 번으로 JSON 하나를 받는다(`ask_json`)
— 분석 세션(오케스트레이터)보다 훨씬 짧고 잦은 호출용. 구독 사용률 확인(`probe_usage`)도 같다.

- `agent_calls` 행은 `agent=<에이전트 이름>` 이라 분석 세션 상한(`calls.sessions_today`,
  orchestrator 만 셈)에 들어가지 않고, 호출자의 `cap`(하루)이 막는다.
- 출력은 stream-json(`runner.parse_stream`) — 구독 사용률(`rate_limit_event`)도 같이 남긴다.
- 실패(상한·타임아웃·비JSON·스키마 위반)는 예외 대신 None — 호출자는 '판정 없음'으로 다룬다."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from trader.kernel.platform import claude_bin as claude_bin_mod
from trader.kernel.registry.types import AgentDef
from trader.kernel.session import agents, calls, usage
from trader.kernel.session import runner as runner_mod

logger = logging.getLogger("trader.session.quick")

BUDGET_USD = 0.2
PROBE_AGENT = "usage_probe"
PROBE_MODEL = "haiku"
PROBE_PROMPT = "사용량 확인용 호출입니다. 다른 말 없이 ok 한 단어만 답하세요."
_JSON_OBJ = re.compile(r"\{.*\}", re.S)


def _extract_json(text: str) -> dict | None:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("{") :] if "{" in t else t
    try:
        v = json.loads(t)
        return v if isinstance(v, dict) else None
    except ValueError:
        m = _JSON_OBJ.search(t)
        if not m:
            return None
        try:
            v = json.loads(m.group(0))
            return v if isinstance(v, dict) else None
        except ValueError:
            return None


def _spawn(
    settings: Any,
    *,
    model: str,
    prompt: str,
    stdin: str,
    session_id: str,
    timeout_s: float,
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
    claude_bin: Path | None = None,
    cancel: threading.Event | None = None,
) -> subprocess.CompletedProcess:
    """도구 없는 `claude -p` 1회(stream-json). 임시 cwd·설정 차단·예산 상한은 분석 세션과 같은 원칙.
    예외(타임아웃·취소·실행 실패)는 그대로 올린다 — 호출자가 agent_calls 행을 닫는다."""
    cwd = Path(tempfile.mkdtemp(prefix="trader-quick-"))
    try:
        bin_path = claude_bin or claude_bin_mod.find_claude(settings.claude_bin)
        cmd = [
            str(bin_path), "-p", "--output-format", "stream-json", "--verbose",
            "--model", model, "--tools", "", "--permission-mode", "dontAsk",
            "--setting-sources", "", "--disable-slash-commands",
            "--append-system-prompt", prompt, "--session-id", session_id,
            "--max-budget-usd", str(BUDGET_USD),
        ]  # fmt: skip
        env = {
            **os.environ,
            "CLAUDE_CODE_RETRY_WATCHDOG": "1",
            "PATH": claude_bin_mod.child_path(Path(bin_path)),
        }
        return (runner or runner_mod.run_claude)(
            cmd,
            input=stdin,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=str(cwd),
            shell=False,
            timeout=timeout_s,
            **({"cancel": cancel} if cancel is not None else {}),
        )
    finally:
        shutil.rmtree(cwd, ignore_errors=True)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def ask_json(
    settings: Any,
    conn: sqlite3.Connection,
    *,
    agent: AgentDef,
    payload: dict,
    schema: type[BaseModel],
    cap: int,
    timeout_s: float,
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
    claude_bin: Path | None = None,
    now: Callable[[], datetime] = _utcnow,
    cancel: threading.Event | None = None,
    fake: Callable[[], dict] | None = None,
) -> BaseModel | None:
    """에이전트 md 본문(+정본 스키마)을 시스템 프롬프트로, payload JSON 을 stdin 으로 넘겨 schema 로
    검증한 결과를 돌려준다. None = 판정 없음(상한·실패). FAKE_CLAUDE=1 이면 `fake()` 응답(없으면
    None)."""
    if runner_mod.is_fake():
        return schema.model_validate(fake()) if fake is not None else None
    call_agent = agent.name.replace("-", "_")
    started = now()
    if calls.calls_today(conn, call_agent, started) >= cap:
        logger.info("%s 하루 상한 %d 도달 — 판정 없음", call_agent, cap)
        return None
    loaded = agents.load_agent(agent)
    model = agents.model_for(agent.name, settings.models, loaded.model)
    prompt = agents.with_schema(loaded.prompt, schema)
    session_id = str(uuid.uuid4())
    call_id = calls.start_call(
        conn, None, agent=call_agent, model=model, ticker=str(payload.get("ticker") or ""),
        market=None, session_id=session_id, now=started,
    )  # fmt: skip
    if call_id is None:  # cap 없이 시작하면 항상 행이 생긴다(-O 에서도 검사)
        raise RuntimeError("agent_calls 행을 만들지 못했습니다")
    t0 = time.monotonic()

    def finish(status: str, *, cost=None, error=None, sid=None) -> None:
        calls.finish_call(
            conn, call_id, status=status, session_id=sid, cost=cost,
            duration_ms=int((time.monotonic() - t0) * 1000), error=error,
        )  # fmt: skip

    try:
        proc = _spawn(
            settings, model=model, prompt=prompt,
            stdin=json.dumps(payload, ensure_ascii=False, default=str), session_id=session_id,
            timeout_s=timeout_s, runner=runner, claude_bin=claude_bin, cancel=cancel,
        )  # fmt: skip
    except runner_mod.SessionCancelled:  # TimeoutExpired 의 하위라 먼저 잡는다
        finish("error", error="데몬 종료 요청으로 중단")
        return None
    except subprocess.TimeoutExpired:
        finish("error", error=f"타임아웃 ({timeout_s}s)")
        return None
    except Exception as e:  # noqa: BLE001 - 판정 실패는 '판정 없음'
        finish("error", error=f"{type(e).__name__}: {e}"[:500])
        return None
    try:
        data, rate = runner_mod.parse_stream(proc.stdout)
    except runner_mod.PipelineError as e:
        finish("error", error=str(e)[:500])
        return None
    if rate is not None:
        try:
            usage.record_rate_limit(conn, rate, now=now())
        except Exception:  # noqa: BLE001 - 사용률은 부가 정보
            logger.debug("사용률 기록 실패", exc_info=True)
    cost, sid = data.get("total_cost_usd"), data.get("session_id")
    if data.get("is_error") or proc.returncode != 0:
        finish("error", cost=cost, error=str(data.get("result", ""))[:500], sid=sid)
        return None
    obj = _extract_json(str(data.get("result", "")))
    try:
        out = schema.model_validate(obj or {})
    except ValidationError as e:
        finish("error", cost=cost, error=f"스키마 위반: {e.error_count()}건", sid=sid)
        return None
    finish("ok", cost=cost, sid=sid)
    return out


def probe_usage(
    settings: Any,
    conn: sqlite3.Connection,
    *,
    timeout_s: float = 90.0,
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
    claude_bin: Path | None = None,
    now: Callable[[], datetime] = _utcnow,
    cancel: threading.Event | None = None,
) -> dict:
    """구독 사용률 확인: 아주 짧은 `claude -p`(haiku) 1회의 `rate_limit_event` 를
    `llm_rate_limits` 에 `source='probe'` 로 남긴다. `agent_calls.agent='usage_probe'` 라 분석
    세션 상한·하루 세션 수에 들어가지 않는다. FAKE_CLAUDE=1 이면 호출 없음."""
    if runner_mod.is_fake():
        return {"skipped": "FAKE"}
    started = now()
    session_id = str(uuid.uuid4())
    call_id = calls.start_call(
        conn, None, agent=PROBE_AGENT, model=PROBE_MODEL, ticker=None, market=None,
        session_id=session_id, now=started,
    )  # fmt: skip
    if call_id is None:  # -O 에서도 검사
        raise RuntimeError("agent_calls 행을 만들지 못했습니다")
    t0 = time.monotonic()

    def finish(status: str, *, cost=None, error=None) -> None:
        calls.finish_call(
            conn, call_id, status=status, cost=cost,
            duration_ms=int((time.monotonic() - t0) * 1000), error=error,
        )  # fmt: skip

    try:
        proc = _spawn(
            settings, model=PROBE_MODEL, prompt=PROBE_PROMPT, stdin="ok", session_id=session_id,
            timeout_s=timeout_s, runner=runner, claude_bin=claude_bin, cancel=cancel,
        )  # fmt: skip
        data, rate = runner_mod.parse_stream(proc.stdout)
    except Exception as e:  # noqa: BLE001 - 확인 실패는 다음 주기에
        finish("error", error=f"{type(e).__name__}: {e}"[:500])
        return {"error": f"{type(e).__name__}: {e}"}
    if rate is None:
        finish("error", cost=data.get("total_cost_usd"), error="rate_limit_event 없음")
        return {"error": "rate_limit_event 없음"}
    try:
        usage.record_rate_limit(conn, rate, now=now(), source="probe")
    except Exception as e:  # noqa: BLE001 - 기록 실패도 행을 running 으로 남기지 않는다
        finish("error", cost=data.get("total_cost_usd"), error=f"{type(e).__name__}: {e}"[:500])
        return {"error": f"{type(e).__name__}: {e}"}
    finish("ok", cost=data.get("total_cost_usd"))
    return {"ok": True}
