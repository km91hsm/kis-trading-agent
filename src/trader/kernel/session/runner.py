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
from dataclasses import dataclass, field, replace
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
from trader.kernel.session import calls, research_policy, usage

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
    guard_parent_signal = kw.pop("terminate_on_parent_signal", False)
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
    old_handler = None
    if guard_parent_signal and threading.current_thread() is threading.main_thread():
        old_handler = signal.getsignal(signal.SIGTERM)

        def on_parent_termination(signum, frame):
            terminate_group(proc)
            raise SessionCancelled(cmd, timeout or 0)

        signal.signal(signal.SIGTERM, on_parent_termination)
    try:
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
            except SessionCancelled:
                raise
            except subprocess.TimeoutExpired:
                pending_input = None  # 첫 호출에서 stdin 은 이미 보냈다
                if cancel is not None and cancel.is_set():
                    terminate_group(proc)
                    raise SessionCancelled(cmd, timeout or 0) from None
                if deadline is not None and time.monotonic() >= deadline:
                    terminate_group(proc)
                    raise
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
    finally:
        if old_handler is not None:
            signal.signal(signal.SIGTERM, old_handler)


def _guard_repeated_timeouts(conn, spec: SessionSpec, kind: SessionKind, settings: Any) -> None:
    """같은 운영일 같은 종목이 타임아웃으로 `timeout_retry_max` 번 실패했으면 다시 띄우지 않는다 —
    600초씩 같은 종목을 반복하며 구독만 쓰던 문제(2026-10-29, 000660 3회). `kind.retry_guard` 를
    선언한 세션 종류만 막는다."""
    if conn is None or not kind.retry_guard or len(spec.tickers) != 1:
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


_LEGACY_LEAD = (
    "bull은 선언된 Claude 에이전트, bear는 Codex 전용 도구다. Claude bear를 쓰지 않는다. "
)
_CODEX_HEADER = "\n# 고정된 Codex 토론 정책 (기존 흐름보다 우선)\n"
# 쟁점·반박·거시 규칙 문단(레거시·교차 공통). 레거시 절은 업데이트 전과 바이트 단위로 같아야 한다
_DEBATE_RULES = (
    "r0은 양측 독립 의견. r1부터 고정 debate_rounds까지 양측"
    "이 상대의 이전 라운드를 반박해야 한다. 각 반박 턴 rebuttals는"
    " 비우지 않는다. "
    "각 공급자는 자기 issues 1~5개(issue_id는 bull:/be"
    "ar: 고유ID, topic,claim,evidence=[리포트 sour"
    "ce_id])를 제시한다. r0 responses/replies는 빈 목"
    "록. "
    "r1+는 자신의 쟁점ID를 유지하고 상대 직전 issues의 모든 ID에"
    " responses(issue_id,response,evidence)로 "
    "반론한다. r2+는 상대 직전 responses가 지적한 자신의 쟁점 전"
    "부에 replies로 재답변한다. "
    "WTI/DXY/미국10년금리 변화가 언제나 주가악재라는 고정규칙은 금지한"
    "다. 성장/인플레/공급원인, 기대반영, 업종별 상반효과와 반례를 검토한다"
    ". "
    "애널리스트 합의라도 토론을 생략하지 않는다. Codex 오류/상한/미완료"
    "면 중단하고 오류를 보고한다. 재개 세션의 기존 결정은 원래 정책을 유지"
    "한다.\n"
)


def codex_section(research: list[tuple[Path, Any, dict]], *, cross: bool) -> str:
    """오케스트레이터 프롬프트에 붙이는 고정 Codex 토론 정책 절. `cross=False` 는 레거시(v1:
    Claude 강세 vs Codex 약세 한 판), `cross=True` 는 4인 교차 토론(판 두 개)."""
    tool = bundle.tool_command()
    if not cross:
        descriptors = {scope.scope: scope for _, scope, policy in research if policy["enabled"]}
        text = _CODEX_HEADER
        text += _LEGACY_LEAD
        text += _DEBATE_RULES
        for descriptor in descriptors.values():
            text += f"- {descriptor.scope} bull: {descriptor.bull_agent}; "
            text += f"bear: {tool} {descriptor.cli_template}\n"
        text += (
            "전용 도구는 입력 묶음을 Python에서 조립하고 JSON검증 후 저장한"
            "다. 각 라운드의 bull과 bear 완료 후 다음 라운드, 마지막에 s"
            "ummarize를 실행한다.\n"
        )
        return text
    crossed = [(scope, policy) for _, scope, policy in research if research_policy.is_cross(policy)]
    descriptors = {scope.scope: scope for scope, _ in crossed}
    codex_timeout = max(int(policy["config"]["timeout_s"]) for _, policy in crossed)
    # 교차 연구가 여럿이면 N 은 그 고정 timeout 중 최댓값이다
    limit = ("가장 긴 연구 기준 " if len(crossed) > 1 else "") + f"각 최대 {codex_timeout}초"
    text = _CODEX_HEADER
    text += (
        "4인 교차 토론: 판 두 개를 같은 라운드 수로 진행한다. "
        "1판은 Claude 강세 vs Codex 약세, 2판은 Codex 강세 vs Claude 약세다. "
        "쟁점·반박은 판 안에서만 주고받는다(1판 턴은 1판 상대, 2판 턴은 2판 상대). "
    )
    text += _DEBATE_RULES
    for d in descriptors.values():
        cmd = f"{tool} {d.cli_template}"
        text += (
            f"- {d.scope} 1판: 강세 = {d.bull_agent}(Claude), 약세 = {cmd} --side bear. "
            f"2판: 강세 = {cmd} --side bull, 약세 = {d.bear_agent}(Claude). "
            "2판 Claude 턴의 입력 묶음은 각 흐름의 researcher 묶음 명령에 --lane b 를 붙여 받고, "
            f"저장은 판 B 단계 이름 {d.cross_turn_prefix}<n>_<side> 로 한다.\n"
        )
    text += (
        "라운드마다 4턴(1판 강세·약세, 2판 강세·약세)이다. 같은 라운드의 4턴은 직전 라운드만 "
        "보므로 Claude 두 턴(1판 강세·2판 약세)은 병렬로 띄우고, Codex 두 턴(1판 약세·2판 강세)은 "
        f"Bash 로 하나씩 실행한다({limit}). "
        "네 턴이 모두 끝나야 다음 라운드로 가고, 마지막에 summarize를 실행한다. "
        "전용 도구는 입력 묶음을 Python에서 조립하고 JSON검증 후 저장한다. "
        "목록에 없는 흐름은 기존 흐름(Claude 강세·약세)대로 진행한다. "
        "연구마다 researcher 묶음의 `research_policy.lanes` 를 따른다. 판이 하나뿐인 연구는 "
        "1판(정책대로)만 진행하고, 그 연구의 2판 거부는 오류로 보고하되 세션을 중단하지 않는다.\n"
    )
    return text


def bash_timeout_ms(settings: Any, policies: list[dict]) -> int:
    """세션 Bash 도구 제한(ms): 켜진 정책들과 설정의 Codex timeout_s 중 최댓값 + 60초."""
    candidates = [int(p["config"]["timeout_s"]) for p in policies if p.get("enabled")]
    candidates.append(int(settings.llm.codex_debate.timeout_s))
    return (max(candidates) + 60) * 1000


def _codex_failed_research(ctx: Any, spec: SessionSpec, kind: SessionKind) -> list[str]:
    """이 세션의 연구 중 「Codex 도중 실패」 표시가 남은 것(연구 폴더 이름). 조회 실패는 빈 목록."""
    if kind.research_targets is None:
        return []
    try:
        return [
            directory.name
            for directory, scope in kind.research_targets(ctx, spec)
            if research_policy._failure(ctx.conn, directory, scope) is not None
        ]
    except Exception:  # noqa: BLE001 - 재실행 판단 실패가 원래 결과를 가리지 않게
        logger.warning("Codex 도중 실패 표시 조회 실패", exc_info=True)
        return []


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
    """LLM 세션(`_run_session_once`) + Codex 도중 실패면 같은 잡에서 한 번 다시 띄운다(스펙 §5-1,
    사용자 결정 A — 재시작의 `research_policy.start` 가 토론을 보관하고 Claude 2인으로 바꿔 그날
    판단이 빠지지 않게). 재실행이 상한·감속 등으로 거부되면 첫 실패 결과를 그대로 돌려준다."""
    kw = dict(agents=agents, runner=runner, claude_bin=claude_bin, cancel=cancel,
              checkpoint_names=checkpoint_names)  # fmt: skip
    result = _run_session_once(spec, kind, ctx, **kw)
    if result.ok or (cancel is not None and cancel.is_set()):
        return result
    failed = _codex_failed_research(ctx, spec, kind)
    if not failed:
        return result
    logger.warning("Codex 도중 실패(%s) — Claude 강세·약세로 한 번 다시 띄웁니다: %s",
                   ", ".join(failed), result.error)  # fmt: skip
    try:
        return _run_session_once(spec, kind, ctx, **kw)
    except PipelineError as e:
        logger.warning("Codex 실패 뒤 재실행 생략: %s", e)
        return result


def _run_session_once(
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
    cap, _ = usage.session_cap(conn, settings)
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
    research = []
    if not fake and kind.research_targets is not None:
        targets = list(kind.research_targets(ctx, spec))
        # v1(Codex 약세) 미완료 연구가 있는 세션은 통째로 레거시 2인 모드, 아니면 교차 토론
        version = research_policy.session_version(conn, targets)
        for directory, scope in targets:
            policy = research_policy.start(
                conn, settings, directory, scope, now=now(), version=version
            )
            research.append((directory, scope, policy))
    codex_required = any(p["enabled"] for _, _, p in research)
    cross = any(research_policy.is_cross(p) for _, _, p in research)
    # 레거시(v1)만 Claude bear 를 뺀다. 교차 토론은 2판 약세가 Claude bear 라 둘 다 남긴다
    effective_kind = (
        replace(
            kind,
            agents=tuple(
                a
                for a in kind.agents
                if a not in {r.bear_agent for _, r, p in research if p["enabled"]}
            ),
        )
        if codex_required and not cross
        else kind
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
            json.dumps(
                agents_mod.agents_json(effective_kind, agents, settings.models), ensure_ascii=False
            ),
            encoding="utf-8",
        )
        prompt = agents_mod.orchestrator_prompt(
            kind,
            agents,
            debate_rounds=max(p["rounds"] for _, _, p in research)
            if codex_required
            else getattr(settings, "debate_rounds", 0),
            checkpoint_names=checkpoint_names,
        )
        if codex_required:
            prompt += codex_section(research, cross=cross)
        session_id = str(uuid.uuid4())
        cmd = build_command(
            spec, kind, settings, bin_path, agents_file, session_id=session_id, prompt=prompt
        )
        bash_ms = bash_timeout_ms(settings, [p for _, _, p in research])
        # 도구 명령(`python -m trader tool`·번들 `trader tool`)이 풀리도록 그 디렉터리를 PATH 앞에
        env = {
            **os.environ,
            "CLAUDE_CODE_RETRY_WATCHDOG": "1",
            # tool 서브프로세스가 상속한다. "0" 일 때만 실제 주문·승인 요청 (LLM 인자 의존 없음)
            "TRADER_DRY_RUN": "1" if spec.dry_run else "0",
            "TRADER_HOME": str(paths.project_root()),
            "PATH": str(bundle.tool_bin_dir()) + os.pathsep + claude_bin_mod.child_path(bin_path),
            # 최대치는 Claude Code 기본 최대(10분) 아래로 낮추지 않는다
            "BASH_MAX_TIMEOUT_MS": str(max(bash_ms, 600000)),
        }
        if codex_required:
            # Codex 턴(Bash 도구)이 Claude Code 기본 Bash 제한(2분)에 잘리지 않게 — Codex 가 켜진
            # 연구가 있는 세션만. 그 밖의 세션은 기본 제한을 그대로 둔다
            env["BASH_DEFAULT_TIMEOUT_MS"] = str(bash_ms)
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

    # 교차 토론 연구마다 Codex 두 턴씩 라운드 수만큼 더 걸린다 — 연구별 연장을 더한다
    timeout = settings.llm.timeout_for(kind.timeout_key) + sum(
        research_policy.cross_extra_s(settings, p) for _, _, p in research
    )
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
        if not data.get("is_error") and proc.returncode == 0:
            for directory, scope, policy in research:
                if policy["enabled"]:
                    if err := research_policy.completion_error(conn, settings, directory, scope):
                        return fail(err)
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
