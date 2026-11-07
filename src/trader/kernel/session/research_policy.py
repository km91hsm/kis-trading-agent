"""연구 시작의 공급자 정책과 필수 참여 검증. 파일의 provider 주장만으로 증명하지 않는다."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from trader.kernel.config import KST, CodexDebateSettings
from trader.kernel.execution.ledger import day_bounds
from trader.kernel.ledger.control import get_flag
from trader.kernel.registry.types import ResearchSpec
from trader.kernel.session import codex, input_receipts, tooling
from trader.kernel.session.run_store import (
    checkpoint_payload,
    list_checkpoints,
    read_checkpoint,
    write_checkpoint,
)
from trader.kernel.session.schemas import DebateTurn

FLAG = "codex_debate_enabled"


class ResearchPolicyError(RuntimeError):
    pass


def configured(conn, settings) -> tuple[bool, str]:
    cfg = getattr(getattr(settings, "llm", None), "codex_debate", CodexDebateSettings())
    override = get_flag(conn, FLAG) if conn is not None else None
    return (bool(override), "control") if override is not None else (cfg.enabled, "yaml")


def current(conn, settings) -> dict:
    enabled, _ = configured(conn, settings)
    cfg = getattr(getattr(settings, "llm", None), "codex_debate", CodexDebateSettings())
    return {
        "version": 1,
        "enabled": enabled,
        "rounds": max(1, settings.debate_rounds) if enabled else settings.debate_rounds,
        "bull": "claude",
        "bear": "codex" if enabled else "claude",
        "config": cfg.model_dump(),
        "claude_models": dict(getattr(settings, "models", {})),
        "prompt_hash": codex.digest(codex.PROMPT),
        "schema_hash": codex.digest(codex.output_schema()),
    }


def key(d: Path, scope: ResearchSpec) -> str:
    return scope.scope + ":" + str(d.resolve())


def _stored(conn, d, scope):
    if conn is None:
        return None
    try:
        row = conn.execute(
            (
                "SELECT policy_json,policy_hash,context_h"
                "ash FROM research_policies WHERE researc"
                "h_key=?"
            ),
            (key(d, scope),),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return {**json.loads(row[0]), "policy_hash": row[1], "context_hash": row[2]} if row else None


def start(conn, settings, d: Path, scope: ResearchSpec, *, now: datetime) -> dict:
    wanted = current(conn, settings)
    wanted["research"] = {
        name: getattr(scope, name)
        for name in (
            "scope",
            "input_stage",
            "report_prefix",
            "turn_prefix",
            "completed_stage",
            "summary_stage",
            "cli_template",
            "bull_agent",
            "bear_agent",
        )
    }
    old = _stored(conn, d, scope)
    analyst_prefix, debate_prefix, completed = (
        scope.report_prefix,
        scope.turn_prefix,
        scope.completed_stage,
    )
    stages = list_checkpoints(d)
    if completed in stages:
        return {
            **(old or wanted),
            "enabled": False,
            "legacy_completed": old is None,
            "resume_completed": True,
        }
    if old:
        # A running session uses get(), never start(): toggles only affect subsequent starts.
        if old["policy_hash"] != codex.digest(wanted):
            raise ResearchPolicyError(
                "미완료 연구의 공급자 정책이 변경되었습니다. "
                "기존 자료를 보존하고 새 연구로 재분석하세요"
            )
        return old
    if wanted["enabled"] and any(s.startswith((analyst_prefix, debate_prefix)) for s in stages):
        raise ResearchPolicyError(
            "기존 미완료 연구에는 Codex 참여 증명이 없습니다. "
            "기존 자료를 보존하고 새 연구로 재분석하세요"
        )
    if conn is None:
        if wanted["enabled"]:
            raise ResearchPolicyError("Codex 정책을 기록할 원장이 없습니다")
        return wanted
    policy_hash = codex.digest(wanted)
    with conn:
        conn.execute(
            (
                "INSERT INTO research_policies(research_k"
                "ey,policy_json,policy_hash,created_at) V"
                "ALUES(?,?,?,?)"
            ),
            (key(d, scope), codex.canonical(wanted), policy_hash, now.isoformat()),
        )
    return {**wanted, "policy_hash": policy_hash, "context_hash": None}


def get(conn, settings, d, scope) -> dict:
    if os.environ.get("FAKE_CLAUDE") == "1":
        return {
            **current(conn, settings),
            "enabled": False,
            "rounds": settings.debate_rounds,
            "fake": True,
        }
    old = _stored(conn, d, scope)
    if old:
        return old
    policy = current(conn, settings)
    # Legacy completed decisions remain legacy even after toggling ON.
    if read_checkpoint(d, scope.completed_stage) is not None:
        return {**policy, "enabled": False, "legacy_completed": True}
    if policy["enabled"]:
        raise ResearchPolicyError(
            "새 연구 세션의 Codex 정책이 없습니다. 기존 자료를 보존하고 새 연구로 재분석하세요"
        )
    return policy


def reports(d, scope):
    prefix = scope.report_prefix
    return [
        (read_checkpoint(d, s) or {}).get("data", {})
        for s in list_checkpoints(d)
        if s.startswith(prefix)
    ]


def _context_hash(d, scope):
    return codex.digest(
        {"inputs": read_checkpoint(d, scope.input_stage), "reports": reports(d, scope)}
    )


def _bind(conn, d, scope, policy):
    h = _context_hash(d, scope)
    if policy.get("context_hash") and policy["context_hash"] != h:
        raise ResearchPolicyError(
            "연구 입력/리포트가 변경되어 Codex 참여 증명이 무효입니다. 새 연구로 재분석하세요"
        )
    if not policy.get("context_hash"):
        with conn:
            conn.execute(
                (
                    "UPDATE research_policies SET context_has"
                    "h=? WHERE research_key=? AND context_has"
                    "h IS NULL"
                ),
                (h, key(d, scope)),
            )
        stored = _stored(conn, d, scope)
        if not stored or stored["context_hash"] != h:
            raise ResearchPolicyError("동시 연구의 입력이 변경되었습니다. 새 연구로 재분석하세요")
        policy["context_hash"] = h


def write_error(conn, settings, d, scope, *, round_no, side) -> str | None:
    try:
        policy = get(conn, settings, d, scope)
        if not policy["enabled"]:
            return None
        if side == "bear":
            return f"Codex bear는 전용 도구로만 기록할 수 있습니다: {scope.cli_template}"
        if round_no > policy["rounds"]:
            return f"라운드 {round_no}는 고정 정책 라운드({policy['rounds']})를 넘습니다"
        _bind(conn, d, scope, policy)
        if round_no > 0:
            for prev_side in ("bull", "bear"):
                if read_checkpoint(d, f"{scope.turn_prefix}{round_no - 1}_{prev_side}") is None:
                    return "반박 이전 라운드의 양측 턴이 필요합니다"
        return None
    except ResearchPolicyError as exc:
        return str(exc)


def rounds(conn, settings, d, scope) -> int:
    return int(get(conn, settings, d, scope)["rounds"])


def payload(conn, settings, d, scope, round_no):
    policy = get(conn, settings, d, scope)
    inputs = read_checkpoint(d, scope.input_stage) or {}
    inputs = inputs.get("data", inputs)
    # Only the previous round is included, so round0 remains independent.
    prefix = scope.turn_prefix
    previous = {}
    if round_no:
        for side in ("bull", "bear"):
            saved = read_checkpoint(d, f"{prefix}{round_no - 1}_{side}")
            if saved is None:
                raise ResearchPolicyError("반박 이전 라운드의 양측 턴이 필요합니다")
            previous[side] = saved.get("data", {})
    metadata = scope.metadata(d, inputs)
    required = {"market", "ticker", "currency", "asof"}
    if not required <= metadata.keys() or any(not metadata[k] for k in required):
        raise ResearchPolicyError(
            "연구 metadata adapter에 market/ticker/currency/asof가 필요합니다"
        )
    data = {
        "scope": scope.scope,
        **metadata,
        "round": round_no,
        "side": "bear",
        "reports": reports(d, scope),
        "own_previous": previous.get("bear"),
        "opponent_previous": previous.get("bull"),
        "input_context": tooling.compact_context(inputs),
    }
    try:
        return tooling.compact_debate(codex.sanitize(data)), policy
    except ValueError as exc:
        raise ResearchPolicyError(str(exc)) from exc


def generate_turn(ctx, d, scope, round_no, *, runner=None, bin_path=None, cancel=None):
    try:
        data, policy = payload(ctx.conn, ctx.settings, d, scope, round_no)
        if not policy["enabled"]:
            return {"ok": False, "error": "Codex 토론 참여가 꺼진 연구입니다"}
        if not 0 <= round_no <= policy["rounds"]:
            return {"ok": False, "error": "고정 정책의 라운드 범위를 벗어났습니다"}
        _bind(ctx.conn, d, scope, policy)
        if isinstance(data.get("input_context"), dict):
            input_receipts.record(
                d,
                data["input_context"],
                stage=f"{scope.scope}:researcher:r{round_no}:bear",
                provider="codex",
                recorded_at=ctx.now().isoformat(),
            )
        result = codex.run_turn(
            ctx.conn,
            settings=ctx.settings,
            policy_hash=policy["policy_hash"],
            data=data,
            config=policy["config"],
            now=ctx.now(),
            runner=runner,
            bin_path=bin_path,
            cancel=cancel,
            validate=lambda turn: turn_error(ctx.conn, ctx.settings, d, scope, turn),
        )
        stage = f"{scope.turn_prefix}{round_no}_bear"
        result["call"]["agent"] = scope.bear_agent
        invalidate_summary(d, scope)
        path = write_checkpoint(d, stage, checkpoint_payload(result["data"], result["call"]))
        return {"ok": True, "path": str(path), "provider": "codex", "round": round_no}
    except (ResearchPolicyError, codex.CodexError) as exc:
        return {"ok": False, "error": str(exc)}


def completion_error(conn, settings, d, scope) -> str | None:
    try:
        policy = get(conn, settings, d, scope)
        if not policy["enabled"]:
            return None
        prefix = scope.turn_prefix
        # Check presence before fingerprint, yielding the useful missing-turn error first.
        for rnd in range(policy["rounds"] + 1):
            for side in ("bull", "bear"):
                cp = read_checkpoint(d, f"{prefix}{rnd}_{side}")
                if cp is None:
                    return f"Codex 필수 토론 미완료: r{rnd}_{side}"
        if policy.get("context_hash") != _context_hash(d, scope):
            return "Codex 입력/리포트 증명 불일치: 새 연구로 재분석하세요"
        for rnd in range(policy["rounds"] + 1):
            for side in ("bull", "bear"):
                candidate = read_checkpoint(d, f"{prefix}{rnd}_{side}") or {}
                if err := turn_error(conn, settings, d, scope, candidate.get("data") or {}):
                    return err
            bull = read_checkpoint(d, f"{prefix}{rnd}_bull") or {}
            if (bull.get("call") or {}).get("provider") != "claude":
                return f"Claude bull 공급자 증명 없음: r{rnd}"
            cp = read_checkpoint(d, f"{prefix}{rnd}_bear") or {}
            turn = DebateTurn.model_validate(cp.get("data"))
            call = cp.get("call") or {}
            row = conn.execute(
                ("SELECT * FROM research_calls WHERE id=? AND provider='codex' AND status='ok'"),
                (call.get("call_id"),),
            ).fetchone()
            expected, _ = payload(conn, settings, d, scope, rnd)
            if (
                turn.side != "bear"
                or turn.round != rnd
                or row is None
                or row["policy_hash"] != policy["policy_hash"]
                or row["model"] != policy["config"]["model"]
                or row["prompt_hash"] != codex.digest(codex.PROMPT)
                or row["schema_hash"] != codex.digest(codex.output_schema())
                or row["input_hash"] != codex.digest(expected)
                or row["turn_hash"] != codex.digest(cp["data"])
                or any(
                    call.get(k) != row[k]
                    for k in ("policy_hash", "input_hash", "prompt_hash", "schema_hash")
                )
            ):
                return f"Codex 참여 증명 불일치: r{rnd}_bear, 새 연구로 재분석하세요"
        return None
    except ResearchPolicyError as exc:
        return f"Codex 참여 검증 실패: {exc}"
    except (ValueError, sqlite3.Error) as exc:
        return f"Codex 참여 검증 실패: {type(exc).__name__}"


def status(conn, settings, *, now):
    from trader.kernel.platform import codex_bin

    enabled, source = configured(conn, settings)
    cfg = getattr(settings.llm, "codex_debate", CodexDebateSettings())
    try:
        codex_bin.find_codex(cfg.bin)
        available = True
    except codex_bin.CodexNotFound:
        available = False
    lo, hi = day_bounds(now.astimezone(KST).date(), "KR")
    row = conn.execute(
        (
            "SELECT COALESCE(SUM(counts_toward_cap),0),SUM(status='error'),SUM("
            "duration_ms),SUM(input_tokens),SUM(outpu"
            "t_tokens) FROM research_calls WHERE prov"
            "ider='codex' AND created_at>=? AND creat"
            "ed_at<?"
        ),
        (lo, hi),
    ).fetchone()
    last = conn.execute(
        "SELECT error FROM research_calls WHERE p"
        "rovider='codex' AND status='error' ORDER"
        " BY id DESC LIMIT 1"
    ).fetchone()
    return {
        "enabled": enabled,
        "source": source,
        "model": cfg.model,
        "available": available,
        "readiness": "installed" if available else "missing",
        "effective_rounds": max(1, getattr(settings, "debate_rounds", 0)),
        "applies_to": "next_new_research",
        "roles": {"bull": "claude", "bear": "codex" if enabled else "claude"},
        "usage": {
            "provider": "codex",
            "calls": row[0],
            "errors": row[1] or 0,
            "duration_ms": row[2] or 0,
            "input_tokens": row[3],
            "output_tokens": row[4],
            "cost_usd": None,
            "subscription_utilization": None,
            "daily_call_cap": cfg.daily_call_cap,
        },
        "last_error": last[0] if last else None,
    }


def turn_error(conn, settings, d, scope, value):
    try:
        policy = get(conn, settings, d, scope)
        if not policy["enabled"]:
            return None
        turn = DebateTurn.model_validate(value)
        issues = turn.issues
        ids = [issue.issue_id for issue in issues]
        if (
            not issues
            or len(ids) != len(set(ids))
            or any(not i.startswith(turn.side + ":") for i in ids)
        ):
            return "각 공급자는 자기 쟁점 1~5개와 고유 side:issue ID를 제시해야 합니다"
        evidence = {
            e.get("source_id")
            for report in reports(d, scope)
            for e in report.get("evidence", [])
            if isinstance(e, dict)
        }
        if any(
            not set(item.evidence) <= evidence for item in [*issues, *turn.responses, *turn.replies]
        ):
            return "쟁점/반론 evidence는 실제 리포트 source_id를 사용해야 합니다"
        if turn.round == 0:
            if turn.responses or turn.replies:
                return "독립 r0은 상대 responses/replies를 참조할 수 없습니다"
            return None
        if not turn.rebuttals:
            return "상호 반박 라운드 rebuttals를 비울 수 없습니다"
        prefix = scope.turn_prefix
        own = (read_checkpoint(d, f"{prefix}{turn.round - 1}_{turn.side}") or {}).get("data") or {}
        opponent = (
            read_checkpoint(
                d, f"{prefix}{turn.round - 1}_{'bear' if turn.side == 'bull' else 'bull'}"
            )
            or {}
        ).get("data") or {}
        if set(ids) != {item["issue_id"] for item in own.get("issues", [])}:
            return "자신의 이전 쟁점 ID를 유지해야 합니다"
        wanted = {item["issue_id"] for item in opponent.get("issues", [])}
        got = [item.issue_id for item in turn.responses]
        if not wanted or set(got) != wanted or len(got) != len(wanted):
            return "상대 쟁점별 반론 coverage가 부족하거나 중복되었습니다"
        reply_wanted = {item["issue_id"] for item in opponent.get("responses", [])}
        replies = [item.issue_id for item in turn.replies]
        if set(replies) != reply_wanted or len(replies) != len(reply_wanted):
            return "상대 반론에 대한 재답변 coverage가 부족하거나 중복되었습니다"
        return None
    except ResearchPolicyError as exc:
        return f"쟁점 토론 검증 실패: {exc}"
    except (ValueError, KeyError) as exc:
        return f"쟁점 토론 검증 실패: {type(exc).__name__}"


def public_policy(conn, settings, d, scope):
    policy = get(conn, settings, d, scope)
    return {k: policy[k] for k in ("enabled", "rounds", "bull", "bear")}


def invalidate_summary(d: Path, research: ResearchSpec) -> bool:
    """턴 변경 전에 그 턴들의 파생 요약만 무효화한다. 결정/주문에는 손대지 않는다."""
    path = d / f"{research.summary_stage}.json"
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed
