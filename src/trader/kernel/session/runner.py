"""LLM 세션 실행: `claude -p` 를 띄우고 결과를 원장(agent_calls·runs)에 기록한다.

- 서브에이전트 정의는 `--agents <json 파일>` 로 명시 전달(`--setting-sources ""` 가 프로젝트
  설정을 끊기 때문). 도구는 `--allowedTools "Bash(<tool_command> *)"` 만. cwd 는 빈 임시 디렉터리.
- 세션 종류별 차이(에이전트·프롬프트 절·타임아웃·상한·감속 등급·FAKE·stdin 보충)는 모두
  `SessionKind` 가 정한다. 커널은 기능 이름을 모른다.
- `FAKE_CLAUDE=1` 이면 subprocess 없이 `kind.fake(ctx, spec)` 가 체크포인트를 쓴다.
- 세션 1회 = `agent_calls` 1행 + `runs` 1행. 일일 세션 상한 초과는 PipelineError.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from trader.kernel.config import ConfigError
from trader.kernel.execution.ledger import day_bounds
from trader.kernel.ledger import runs
from trader.kernel.platform import bundle, paths, procs
from trader.kernel.platform import claude_bin as claude_bin_mod
from trader.kernel.registry.types import AgentDef, SessionKind
from trader.kernel.session import agents as agents_mod
from trader.kernel.session import calls, usage

logger = logging.getLogger("trader.session")


class PipelineError(RuntimeError):
    """세션을 띄울 수 없거나(상한·설정) 파이프라인 전제가 깨졌을 때."""


class ThrottleBlocked(PipelineError):
    """구독 사용률 감속으로 세션을 띄우지 않았다 — 실패가 아니라 '생략'(데몬·대기열이 skipped
    로 센다)."""


@dataclass
class SessionSpec:
    market: str
    date: date
    tickers: list[dict[str, Any]]  # [{"ticker": str, "exchange": str | None}]
    dry_run: bool = True
    asof: datetime | None = None  # tool collect 에 넘길 기준 시각 (없으면 세션 시작 시각)
    kind: str = "analyze"
    # 레인별 보조 입력(후보·맥락·트리거·포지션 …). 각 `SessionKind.stdin_extra(spec)` 가 꺼내 쓴다
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionResult:
    ok: bool
    session_id: str | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None
    num_turns: int | None = None
    error: str | None = None
    fake: bool = False
    stages_written: list[str] = field(default_factory=list)
    result_text: str | None = None


def is_fake() -> bool:
    return os.environ.get("FAKE_CLAUDE") == "1"


def build_command(
    spec: SessionSpec,
    kind: SessionKind,
    settings: Any,
    bin_path: Path,
    agents_file: Path,
    *,
    session_id: str,
    prompt: str,
) -> list[str]:
    """설치된 claude 2.1.x 도움말에 있는 플래그만 쓴다. --bare·--max-turns 금지."""
    model = settings.models.get("orchestrator", "sonnet")
    return [
        str(bin_path),
        "-p",
        "--output-format",
        "stream-json",  # 결과 줄 + rate_limit_event(구독 사용률) — json 결과에는 사용률이 없다
        "--verbose",  # stream-json 은 -p 에서 --verbose 가 있어야 한다
        "--model",
        model,
        "--tools",
        "Bash,Agent",
        "--allowedTools",
        f"Bash({bundle.tool_command()} *)",  # 프롬프트의 명령 문자열과 같아야 한다
        "--permission-mode",
        "dontAsk",
        "--setting-sources",
        "",
        "--disable-slash-commands",
        "--agents",
        str(agents_file),
        "--append-system-prompt",
        prompt,
        "--session-id",
        session_id,
        "--max-budget-usd",
        str(settings.llm.budget_usd),
    ]


def stdin_payload(spec: SessionSpec, kind: SessionKind, asof: datetime) -> str:
    """세션 stdin JSON: 기본 6키 + 종류별 보충(`kind.stdin_extra`)."""
    payload: dict[str, Any] = {
        "kind": kind.kind,
        "market": spec.market,
        "date": spec.date.isoformat(),
        "tickers": spec.tickers,
        "dry_run": spec.dry_run,
        "asof": asof.isoformat(),
    }
    if kind.stdin_extra is not None:
        payload.update(kind.stdin_extra(spec))
    return json.dumps(payload, ensure_ascii=False, default=str)


def parse_stream(stdout: str) -> tuple[dict, dict | None]:
    """claude -p 출력 → (결과 객체, 마지막 rate_limit_info | None).

    stream-json(JSONL)이면 마지막 `type == "result"` 줄과 마지막 `rate_limit_event` 를 고른다.
    단일 JSON 객체(옛 `--output-format json`·테스트 러너)도 그대로 받는다. 결과 줄이 없으면
    (중간에 죽음) PipelineError — 호출자가 세션을 error 로 닫는다."""
    text = stdout.strip()
    try:
        whole = json.loads(text)
    except ValueError:
        whole = None
    if isinstance(whole, dict):
        return whole, None
    result: dict | None = None
    rate: dict | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "result":
            result = ev
        elif ev.get("type") == "rate_limit_event" and isinstance(ev.get("rate_limit_info"), dict):
            rate = ev["rate_limit_info"]
    if result is None:
        raise PipelineError("claude 출력에 결과(result) JSON 줄이 없습니다")
    return result, rate


TERM_GRACE_S = 10.0  # SIGTERM 뒤 `trader tool` 이 주문 기록을 마칠 유예
KILL_WAIT_S = 5.0
CANCEL_POLL_S = 1.0


class SessionCancelled(subprocess.TimeoutExpired):
    """종료 요청(stop_event)으로 세션을 중단했다. TimeoutExpired 의 하위라 기존 처리 경로를 탄다."""


WIN_NEW_GROUP_NO_WINDOW = 0x00000200 | 0x08000000  # CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW


def _signal_group(proc: subprocess.Popen, sig: int) -> None:
    if os.name == "posix":
        try:
            os.killpg(proc.pid, sig)
            return
        except ProcessLookupError:
            return
        except PermissionError:
            pass
    try:
        proc.terminate() if sig == signal.SIGTERM else proc.kill()
    except ProcessLookupError:
        pass


def _drain(proc: subprocess.Popen, timeout: float) -> bool:
    try:
        proc.communicate(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        return False


def terminate_group(proc: subprocess.Popen) -> None:
    """SIGTERM → 유예(TERM_GRACE_S) → SIGKILL → 짧은 대기 → 파이프 닫기. 어느 단계도 무한히
    기다리지 않는다(그룹을 벗어난 손자가 파이프를 쥐고 있어도 돌아온다)."""
    if os.name != "posix":
        # Windows: 프로세스 그룹 신호·SIGKILL 이 없다. claude.cmd 면 Popen 이 쥔 것은 cmd.exe 라
        # terminate 만으로는 node(claude)·`trader tool` 자식이 살아남아 주문을 이어 갈 수 있다 —
        # 부모가 살아 있을 때 `taskkill /T /F` 로 트리 전체를 정리한다(부모를 먼저 끝내면 /T 가
        # 자식 트리를 못 찾는다). Windows 콘솔 프로세스엔 정중한 종료 신호도 없다
        procs.kill_group(proc.pid)
    else:
        _signal_group(proc, signal.SIGTERM)
        if _drain(proc, TERM_GRACE_S):
            return
        _signal_group(proc, signal.SIGKILL)
    if not _drain(proc, KILL_WAIT_S):
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass


def run_claude(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess:
    """subprocess.run 과 같은 인터페이스. 새 프로세스 그룹으로 띄우고 타임아웃·취소(`cancel`
    이벤트)면 그룹을 SIGTERM → 유예 → SIGKILL 로 정리한다 — subprocess.run 의 kill 은 claude 만
    죽여 그 자식(`trader tool` 서브프로세스)이 고아로 남아 결정·주문을 계속 낼 수 있고, SIGKILL 을
    바로 보내면 주문 접수 뒤 원장 기록 전에 죽을 수 있다. Windows 는 프로세스 그룹 신호가 없어
    트리를 `taskkill` 로 정리한다. 정리 대기는 유한하다."""
    timeout = kw.pop("timeout", None)
    stdin = kw.pop("input", None)
    cancel: threading.Event | None = kw.pop("cancel", None)
    kw.pop("capture_output", None)
    posix = os.name == "posix"
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=posix,
        # Windows: 새 프로세스 그룹 + 콘솔 창 없음(창 없는 앱에서 세션마다 콘솔이 뜨고, 그 창을
        # 닫으면 세션이 강제 종료되던 문제)
        **({} if posix else {"creationflags": WIN_NEW_GROUP_NO_WINDOW}),
        **kw,
    )
    deadline = None if timeout is None else time.monotonic() + float(timeout)
    pending_input = stdin
    while True:
        step = CANCEL_POLL_S if cancel is not None else None
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= 0:
                terminate_group(proc)
                raise subprocess.TimeoutExpired(cmd, timeout or 0)
            step = left if step is None else min(step, left)
        try:
            out, err = proc.communicate(pending_input, timeout=step)
            break
        except subprocess.TimeoutExpired:
            pending_input = None  # 첫 호출에서 stdin 은 이미 보냈다
            if cancel is not None and cancel.is_set():
                terminate_group(proc)
                raise SessionCancelled(cmd, timeout or 0) from None
            if deadline is not None and time.monotonic() >= deadline:
                terminate_group(proc)
                raise
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _guard_repeated_timeouts(conn, spec: SessionSpec, kind: SessionKind, settings: Any) -> None:
    """같은 운영일 같은 종목이 타임아웃으로 `timeout_retry_max` 번 실패했으면 다시 띄우지 않는다 —
    600초씩 같은 종목을 반복하며 구독만 쓰던 문제(2026-10-29, 000660 3회)."""
    if conn is None or kind.kind != "analyze" or len(spec.tickers) != 1:
        return
    ticker = spec.tickers[0].get("ticker")
    lo, hi = day_bounds(spec.date, spec.market)
    n = conn.execute(
        "SELECT COUNT(*) FROM agent_calls WHERE ticker = ? AND market = ? AND status = 'error' "
        "AND error LIKE '세션 타임아웃%' AND created_at >= ? AND created_at < ?",
        (ticker, spec.market, lo, hi),
    ).fetchone()[0]
    if n >= settings.llm.timeout_retry_max:
        raise PipelineError(f"{ticker} 오늘 세션 타임아웃 {n}회 — 오늘은 다시 띄우지 않습니다")


def _finish(
    conn,
    call_id: int,
    run_id: int,
    *,
    status: str,
    session_id: str | None,
    cost: float | None,
    duration_ms: int | None,
    error: str | None,
    num_turns: int | None = None,
) -> None:
    """agent_calls 행과 runs 행을 함께 닫는다."""
    calls.finish_call(
        conn,
        call_id,
        status=status,
        session_id=session_id,
        cost=cost,
        duration_ms=duration_ms,
        error=error,
        num_turns=num_turns,
    )
    runs.finish(conn, run_id, status, error=error)


def run_session(
    spec: SessionSpec,
    kind: SessionKind,
    ctx: Any,
    *,
    agents: Mapping[str, AgentDef],
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
    claude_bin: Path | None = None,
    cancel: threading.Event | None = None,
    checkpoint_names: Sequence[str] = (),
) -> SessionResult:
    """LLM 세션 1회(`ctx` 는 `LaneContext`). 실패해도 체크포인트는 그대로 남아 재실행 시
    이어간다. 상한·감속·기동 전 단계 실패는 예외, 세션 실행 중 실패는 `ok=False` 결과."""
    settings, conn, now = ctx.settings, ctx.conn, ctx.now
    if settings.anonymize:
        raise ConfigError("settings.anonymize=true 는 Phase 6 전까지 지원하지 않습니다")
    cap = settings.llm.daily_session_cap
    if calls.sessions_today(conn, now()) >= cap:
        raise PipelineError(f"오늘 세션 상한({cap})에 도달해 세션을 띄우지 않습니다")
    fake = is_fake()
    if not fake:
        ok, why = usage.allowance(
            conn, settings, throttle=kind.throttle, label=kind.kind, now=now()
        )
        if not ok:
            raise ThrottleBlocked(why or "구독 사용률 감속")
        _guard_repeated_timeouts(conn, spec, kind, settings)
    extra_cap = (
        (kind.runs_kind, int(getattr(settings.llm, kind.extra_cap_key)))
        if kind.extra_cap_key
        else None
    )
    if extra_cap and calls.sessions_today(conn, now(), kind=extra_cap[0]) >= extra_cap[1]:
        raise PipelineError(
            f"오늘 {extra_cap[0]} 세션 상한({extra_cap[1]})에 도달해 세션을 띄우지 않습니다"
        )
    run_id = runs.start(conn, kind.runs_kind, spec.market)
    started = now()
    tickers = ",".join(t["ticker"] for t in spec.tickers)

    def elapsed_ms() -> int:
        return int((now() - started).total_seconds() * 1000)

    if fake:
        call_id = calls.start_call(
            conn, run_id, agent=calls.ORCHESTRATOR, model=calls.FAKE_MODEL, ticker=tickers,
            market=spec.market, session_id="fake", now=started,
        )  # fmt: skip
        assert call_id is not None  # fake 는 상한을 보지 않는다
        try:
            written = list(kind.fake(ctx, spec) or [])
        except Exception as e:  # noqa: BLE001 - FAKE 는 개발용 — 어떤 실패도 error 결과로 닫는다
            logger.warning("FAKE 세션 실패 %s: %s", spec.market, e)
            _finish(
                conn, call_id, run_id, status="error", session_id="fake", cost=0.0,
                duration_ms=elapsed_ms(), error=str(e),
            )  # fmt: skip
            return SessionResult(ok=False, session_id="fake", fake=True, error=str(e))
        _finish(
            conn, call_id, run_id, status="ok", session_id="fake", cost=0.0,
            duration_ms=elapsed_ms(), error=None,
        )  # fmt: skip
        return SessionResult(
            ok=True,
            session_id="fake",
            cost_usd=0.0,
            duration_ms=elapsed_ms(),
            fake=True,
            stages_written=written,
        )

    cwd: Path | None = None
    try:
        # 호출자가 경로를 넘기면(테스트·dry-run) 그대로 쓰고, 아니면 settings/PATH 에서 찾는다
        bin_path = claude_bin if claude_bin else claude_bin_mod.find_claude(settings.claude_bin)
        # cwd 는 저장소 밖 임시 디렉터리 (claude 가 상위 CLAUDE.md·settings 를 탐색하지 못하게)
        cwd = Path(tempfile.mkdtemp(prefix="trader-claude-"))
        agents_file = cwd / "agents.json"
        agents_file.write_text(
            json.dumps(agents_mod.agents_json(kind, agents, settings.models), ensure_ascii=False),
            encoding="utf-8",
        )
        prompt = agents_mod.orchestrator_prompt(
            kind,
            agents,
            debate_rounds=getattr(settings, "debate_rounds", 0),
            checkpoint_names=checkpoint_names,
        )
        session_id = str(uuid.uuid4())
        cmd = build_command(
            spec, kind, settings, bin_path, agents_file, session_id=session_id, prompt=prompt
        )
        # 도구 명령(`python -m trader tool`·번들 `trader tool`)이 풀리도록 그 디렉터리를 PATH 앞에
        env = {
            **os.environ,
            "CLAUDE_CODE_RETRY_WATCHDOG": "1",
            # tool 서브프로세스가 상속한다. "0" 일 때만 실제 주문·승인 요청 (LLM 인자 의존 없음)
            "TRADER_DRY_RUN": "1" if spec.dry_run else "0",
            "TRADER_HOME": str(paths.project_root()),
            "PATH": str(bundle.tool_bin_dir()) + os.pathsep + claude_bin_mod.child_path(bin_path),
        }
        model = settings.models.get("orchestrator", "sonnet")
        call_id = calls.start_call(
            conn, run_id, agent=calls.ORCHESTRATOR, model=model, ticker=tickers,
            market=spec.market, session_id=session_id, now=started, cap=cap, extra_cap=extra_cap,
        )  # fmt: skip
        if call_id is None:  # 사전 검사 뒤 다른 프로세스가 마지막 자리를 잡았다
            if extra_cap:
                raise PipelineError(
                    f"오늘 {extra_cap[0]} 세션 상한({extra_cap[1]})에 도달해 세션을 띄우지 않습니다"
                )
            raise PipelineError(f"오늘 세션 상한({cap})에 도달해 세션을 띄우지 않습니다")
    except Exception as e:
        # 기동 전 단계(실행파일·에이전트 md·프롬프트·상한) 실패: runs 행이 running 으로 남고 임시
        # cwd 가 누수되지 않게 닫고 지운 뒤 그대로 올린다 (호출자가 ClaudeNotFound·ConfigError 를
        # 구분한다)
        if cwd is not None:
            shutil.rmtree(cwd, ignore_errors=True)
        try:
            runs.finish(conn, run_id, "error", error=f"세션 준비 실패: {type(e).__name__}: {e}")
        except Exception:  # noqa: BLE001 - 원 예외를 가리지 않는다
            logger.warning("runs 종료 기록 실패", exc_info=True)
        raise
    asof = spec.asof or started

    def fail(err: str) -> SessionResult:
        _finish(
            conn, call_id, run_id, status="error", session_id=session_id, cost=None,
            duration_ms=elapsed_ms(), error=err,
        )  # fmt: skip
        return SessionResult(ok=False, session_id=session_id, error=err, duration_ms=elapsed_ms())

    timeout = settings.llm.timeout_for(kind.timeout_key)
    try:
        proc = (runner or run_claude)(
            cmd,
            input=stdin_payload(spec, kind, asof),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            cwd=str(cwd),
            shell=False,
            timeout=timeout,
            **({"cancel": cancel} if cancel is not None else {}),
        )
    except SessionCancelled:
        return fail("세션 중단 (종료 요청)")
    except subprocess.TimeoutExpired:
        return fail(f"세션 타임아웃 ({timeout}s)")
    except OSError as e:  # 실행파일 없음·권한 등: running 행을 남기지 않고 오류로 닫는다
        return fail(f"세션 기동 실패: {type(e).__name__}: {e}")
    except Exception as e:
        # stdin_payload·러너의 임의 예외: running 행을 남기지 않고 error 로 닫은 뒤 그대로 올린다
        fail(f"세션 실행 실패: {type(e).__name__}: {e}")
        raise
    finally:
        shutil.rmtree(cwd, ignore_errors=True)
    try:
        data, rate = parse_stream(proc.stdout)
    except PipelineError as e:
        return fail(f"{e} (exit={proc.returncode}, stderr={proc.stderr[:200]!r})")
    if rate is not None:
        try:
            usage.record_rate_limit(conn, rate, now=now())
        except Exception:  # noqa: BLE001 - 사용률은 부가 정보 — 세션 결과를 막지 않는다
            logger.warning("사용률 기록 실패", exc_info=True)
    try:
        return _finish_session(conn, call_id, run_id, spec, data, proc, session_id, elapsed_ms)
    except Exception as e:  # noqa: BLE001 - 결과 처리 중 예외에도 running 행을 남기지 않는다
        logger.exception("세션 결과 처리 실패 %s", spec.market)
        return fail(f"세션 결과 처리 실패: {type(e).__name__}: {e}")


def _finish_session(
    conn, call_id, run_id, spec: SessionSpec, data: dict, proc, session_id: str, elapsed_ms
) -> SessionResult:
    cost = data.get("total_cost_usd")
    duration = elapsed_ms()  # claude 의 duration_ms 는 마지막 턴 기준이라 파이썬 측 측정값을 쓴다
    sid = str(data.get("session_id") or session_id)
    is_error = bool(data.get("is_error")) or proc.returncode != 0
    result_text = str(data.get("result", ""))
    error = result_text[:500] if is_error else None
    _finish(
        conn,
        call_id,
        run_id,
        status="error" if is_error else "ok",
        session_id=sid,
        cost=cost,
        duration_ms=duration,
        error=error,
        num_turns=data.get("num_turns"),
    )
    logger.info(
        "세션 %s %s: %s cost=%s dur=%sms",
        spec.market,
        [t["ticker"] for t in spec.tickers],
        "error" if is_error else "ok",
        cost,
        duration,
    )
    return SessionResult(
        ok=not is_error,
        session_id=sid,
        cost_usd=cost,
        duration_ms=duration,
        num_turns=data.get("num_turns"),
        error=error,
        result_text=result_text,
    )
