"""연구 시작의 공급자 정책과 필수 참여 검증. 파일의 provider 주장만으로 증명하지 않는다."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
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
# 설정 화면 값(없으면 yaml `llm.codex_debate.usage_throttle`·`daily_call_cap`)
CODEX_THROTTLE_FLAG = "codex_usage_throttle"
CODEX_CAP_FLAG = "codex_daily_call_cap"
CODEX_CAP_RANGE = (1, 10000)
POLICY_EXCLUDED = frozenset({"usage_throttle", "hard_pct"})


class ResearchPolicyError(RuntimeError):
    pass


def configured(conn, settings) -> tuple[bool, str]:
    cfg = getattr(getattr(settings, "llm", None), "codex_debate", CodexDebateSettings())
    override = get_flag(conn, FLAG) if conn is not None else None
    return (bool(override), "control") if override is not None else (cfg.enabled, "yaml")


def _cfg(settings) -> CodexDebateSettings:
    return getattr(getattr(settings, "llm", None), "codex_debate", CodexDebateSettings())


def throttle_enabled(conn, settings) -> tuple[bool, str]:
    """(Codex 사용량 조절 켜짐, 출처 'control'|'yaml'). 화면 값이 yaml 보다 우선."""
    flag = get_flag(conn, CODEX_THROTTLE_FLAG) if conn is not None else None
    if isinstance(flag, bool):
        return flag, "control"
    return bool(_cfg(settings).usage_throttle), "yaml"


def call_cap(conn, settings) -> tuple[int, str]:
    """(Codex 하루 호출 제한, 출처). 화면 값이 범위 안의 정수면 yaml 보다 우선."""
    flag = get_flag(conn, CODEX_CAP_FLAG) if conn is not None else None
    lo, hi = CODEX_CAP_RANGE
    if type(flag) is int and lo <= flag <= hi:
        return flag, "control"
    return int(_cfg(settings).daily_call_cap), "yaml"


def calls_today(conn, now: datetime) -> int:
    """오늘(KST 운영일) 상한에 세는 Codex 호출 수."""
    if conn is None:
        return 0
    lo, hi = day_bounds(now.astimezone(KST).date(), "KR")
    return int(
        conn.execute(
            "SELECT COALESCE(SUM(counts_toward_cap),0) FROM research_calls "
            "WHERE provider='codex' AND created_at>=? AND created_at<?",
            (lo, hi),
        ).fetchone()[0]
    )


def _read_codex_limits(settings, now: datetime) -> dict:
    """Codex 사용률 관측(테스트는 이 훅을 바꾼다 — 실제 `codex` 를 실행하지 않게)."""
    from trader.kernel.session import codex_usage

    return codex_usage.read_limits(_cfg(settings).bin, now=now)


def codex_allowance(
    conn, settings, *, now: datetime, need: int = 1, limits: dict | None = None, probe: bool = True
) -> tuple[bool, str | None]:
    """새 연구에서 Codex 를 써도 되는가. 오늘 호출 + 이 연구에 필요한 호출(need) > 상한이면 늘 거부.
    조절이 켜져 있으면 5시간·7일 사용률 중 하나라도 ≥ hard_pct 일 때도 거부. 사용률은 `limits` 로
    받거나(probe=True 이고 없으면) 직접 읽는다. 읽기 실패·이전 관측(stale)·관측 없음·리셋 지난 창은
    막지 않는다."""
    cap, _ = call_cap(conn, settings)
    used = calls_today(conn, now)
    if used + need > cap:
        return False, f"Codex 오늘 호출 상한({cap}) — 사용 {used}회, 이번 분석에 {need}회 필요"
    if not throttle_enabled(conn, settings)[0] or os.environ.get("FAKE_CLAUDE") == "1":
        return True, None
    if limits is None:
        if not probe:
            return True, None
        try:
            limits = _read_codex_limits(settings, now) or {}
        except Exception:  # noqa: BLE001 — 읽지 못하면 막지 않는다(Claude 쪽과 같은 원칙)
            return True, None
    if limits.get("status") != "ready":
        return True, None
    hard = float(_cfg(settings).hard_pct)
    worst: tuple[str, float] | None = None
    for k, name in (("five_hour", "5시간"), ("seven_day", "7일")):
        w = limits.get(k)
        if not isinstance(w, dict) or not isinstance(w.get("used_pct"), (int, float)):
            continue
        resets = w.get("resets_at")
        try:
            if resets and datetime.fromisoformat(resets) <= now:
                continue  # 이미 리셋된 창
        except ValueError:
            continue
        pct = float(w["used_pct"]) / 100
        if worst is None or pct > worst[1]:
            worst = (name, pct)
    if worst is not None and worst[1] >= hard:
        return False, f"Codex {worst[0]} 사용률 {worst[1]:.0%} ≥ {hard:.0%}"
    return True, None


def current(conn, settings, *, version: int = 2) -> dict:
    """지금 설정의 공급자 정책. version=1 은 레거시(약세만 Codex) 모양 그대로,
    version=2 는 교차 토론 두 판(lanes)을 더한다(Codex 꺼짐이면 Claude 한 판)."""
    enabled, _ = configured(conn, settings)
    cfg = getattr(getattr(settings, "llm", None), "codex_debate", CodexDebateSettings())
    policy = {
        "version": 1,
        "enabled": enabled,
        "rounds": max(1, settings.debate_rounds) if enabled else settings.debate_rounds,
        "bull": "claude",
        "bear": "codex" if enabled else "claude",
        # 조절 설정은 시작 판단 입력일 뿐 공급자 정책이 아니다 — 해시에서 빼서 업데이트·yaml
        # 변경에도 진행 중 연구의 정책 해시가 바뀌지 않게 한다
        "config": cfg.model_dump(exclude=POLICY_EXCLUDED),
        "claude_models": dict(getattr(settings, "models", {})),
        "prompt_hash": codex.digest(codex.PROMPT),
        "schema_hash": codex.digest(codex.output_schema()),
    }
    if version == 1:
        return policy
    policy["version"] = 2
    policy["lanes"] = _v2_lanes(enabled)
    policy["prompt_hashes"] = {
        "bull": codex.digest(codex.BULL_PROMPT),
        "bear": codex.digest(codex.PROMPT),
    }
    return policy


def _v2_lanes(enabled: bool) -> list[dict[str, str]]:
    """v2 판 고정: 켜짐이면 교차 두 판, 꺼짐이면 Claude 한 판."""
    if enabled:
        return [
            {"lane": "a", "bull": "claude", "bear": "codex"},
            {"lane": "b", "bull": "codex", "bear": "claude"},
        ]
    return [{"lane": "a", "bull": "claude", "bear": "claude"}]


def lanes(policy: dict) -> list[dict[str, str]]:
    """정책의 판 목록. v1(판 없음)은 bull/bear 로 판 a 하나를 만든다."""
    if policy.get("lanes"):
        return [dict(lane) for lane in policy["lanes"]]
    return [{"lane": "a", "bull": policy["bull"], "bear": policy["bear"]}]


def provider_for(policy: dict, lane: str, side: str) -> str | None:
    """판·편의 공급자. 정책에 그 판이 없으면 None."""
    for item in lanes(policy):
        if item["lane"] == lane:
            return item.get(side)
    return None


def codex_slots(policy: dict) -> list[tuple[str, str]]:
    """공급자가 codex 인 (판, 편) 목록."""
    return [
        (item["lane"], side)
        for item in lanes(policy)
        for side in ("bull", "bear")
        if item[side] == "codex"
    ]


def codex_need(policy: dict) -> int:
    """이 정책의 연구 하나가 쓸 Codex 호출 수 — Codex 자리마다 라운드당 1회(0~rounds).
    v2 교차는 2×(rounds+1), v1 은 rounds+1."""
    return len(codex_slots(policy)) * (int(policy["rounds"]) + 1)


def is_cross(policy: dict) -> bool:
    """교차 토론(v2·Codex 켜짐·두 판) 정책인가."""
    return policy.get("version") == 2 and bool(policy["enabled"]) and len(lanes(policy)) == 2


def session_version(conn, targets: list[tuple[Path, ResearchSpec]]) -> int:
    """세션의 정책 버전. 저장된 v1(Codex 켜짐) 미완료 연구가 하나라도 있으면 그 세션은
    레거시 2인 모드(1)로 돌고 새 연구도 v1 로 시작한다. 아니면 교차 토론 모드(2)."""
    for d, scope in targets:
        old = _stored(conn, d, scope)
        if (
            old
            and int(old.get("version") or 1) == 1
            and old.get("enabled")
            and scope.completed_stage not in list_checkpoints(d)
        ):
            return 1
    return 2


def cross_extra_s(settings, policy: dict) -> int:
    """교차 토론 연구 하나가 세션 제한 시간에 더할 초. 교차가 아니면 0, 설정값이 있으면 그 값,
    없으면 (라운드+1) × (고정 정책의 Codex timeout_s + 120)."""
    if not is_cross(policy):
        return 0
    override = getattr(getattr(settings, "llm", None), "cross_debate_extra_s", None)
    if override is not None:
        return int(override)
    return (int(policy["rounds"]) + 1) * (int(policy["config"]["timeout_s"]) + 120)


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


def _failure(conn, d: Path, scope: ResearchSpec) -> tuple[str, str] | None:
    """도중 Codex 실패 표시 (시각, 사유). 표시가 없거나 열이 없는 옛 원장이면 None."""
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT codex_failed_at, codex_failed_reason FROM research_policies "
            "WHERE research_key=?",
            (key(d, scope),),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row or not row[0]:
        return None
    return row[0], row[1] or ""


def mark_codex_failed(conn, d: Path, scope: ResearchSpec, *, reason: str, now: datetime) -> None:
    """교차 토론 도중 Codex 실패를 표시한다. 다음 `start` 가 토론을 보관하고
    Claude 2인으로 바꾼다."""
    with conn:
        conn.execute(
            "UPDATE research_policies SET codex_failed_at=?, codex_failed_reason=? "
            "WHERE research_key=?",
            (now.astimezone(UTC).isoformat(), reason, key(d, scope)),
        )


def _archive_dir(d: Path, now: datetime) -> Path:
    """토론 보관 폴더 `_codex_failed_<UTC YYYYMMDD_HHMMSS>`(이미 있으면 `_2`, `_3`…)를 만들어
    돌려준다. 이름에 `:` 를 쓰지 않는다(Windows)."""
    base = f"_codex_failed_{now.astimezone(UTC):%Y%m%d_%H%M%S}"
    n = 1
    while True:
        path = d / (base if n == 1 else f"{base}_{n}")
        try:
            path.mkdir()
            return path
        except FileExistsError:
            n += 1


def _failover(conn, settings, d: Path, scope: ResearchSpec, old: dict, *, research, reason, now):
    """도중 Codex 실패한 교차 연구를 다시 시작할 때: 두 판 토론·요약을 보관 폴더로 옮기고(삭제하지
    않음, 애널리스트·입력은 그대로) 같은 행의 정책을 Claude 1판으로 바꾼다."""
    moving = [
        stage
        for stage in list_checkpoints(d)
        if stage.startswith((scope.turn_prefix, scope.cross_turn_prefix))
        or stage == scope.summary_stage
    ]
    # 옮길 파일이 없으면(이동 뒤 UPDATE 전에 죽은 재시작) 빈 보관 폴더를 만들지 않는다
    archive = _archive_dir(d, now) if moving else None
    for stage in moving:
        (d / f"{stage}.json").replace(archive / f"{stage}.json")
    skipped = f"Codex 도중 실패 — {reason} — 이번 분석은 Claude 강세·약세"
    policy = {
        **current(conn, settings, version=2),
        "enabled": False,
        "rounds": settings.debate_rounds,
        "lanes": _v2_lanes(False),
        "bear": "claude",
        "codex_skipped": skipped,
        "research": research,
    }
    policy_hash = codex.digest(policy)
    with conn:
        conn.execute(
            "UPDATE research_policies SET policy_json=?, policy_hash=?, context_hash=NULL, "
            "codex_failed_at=NULL, replaced_from_hash=?, replaced_reason=? WHERE research_key=?",
            (
                codex.canonical(policy),
                policy_hash,
                old["policy_hash"],
                f"Codex 도중 실패 — {reason}",
                key(d, scope),
            ),
        )
    return {**policy, "policy_hash": policy_hash, "context_hash": None}


def start(conn, settings, d: Path, scope: ResearchSpec, *, now: datetime, version: int = 2) -> dict:
    old_stored = _stored(conn, d, scope)
    # 저장된 정책이 있으면 그 버전으로 비교한다(v1/v2 섞인 세션에서 「정책 변경」 오판 방지)
    wanted = current(
        conn, settings, version=int(old_stored.get("version") or 1) if old_stored else version
    )
    if old_stored and old_stored.get("codex_skipped"):
        # 사용량 조절로 Claude 만 쓰기로 고정된 연구 — 다시 시작해도 그대로(사용률이 바뀌어도)
        return old_stored
    if (
        old_stored is None
        and wanted["enabled"]
        and scope.completed_stage not in list_checkpoints(d)  # 끝난 연구는 판단하지 않는다
    ):
        # Codex 턴은 Codex 자리마다 라운드당 1회(0~rounds) — v2 는 2×(rounds+1), v1 은 rounds+1.
        # 남은 호출이 모자라면 시작부터 Claude 로. 여기서 Claude 로 바뀌면(enabled=False) 아래의
        # 「Codex 참여 증명 없음」·「원장 없음」 거부도 적용되지 않는다 — Codex 증명이 필요 없다.
        need = codex_need(wanted)
        ok, why = codex_allowance(conn, settings, now=now, need=need)
        if not ok:
            wanted = {
                **wanted,
                "enabled": False,
                "rounds": settings.debate_rounds,
                "bear": "claude",
                "codex_skipped": f"{why} — 이번 분석은 Claude 강세·약세",
            }
            if "lanes" in wanted:
                wanted["lanes"] = _v2_lanes(False)
    research_keys = (
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
    if wanted["version"] == 2:
        research_keys += ("cross_turn_prefix",)  # v2 해시에만 영향(v1 해시 모양 유지)
    wanted["research"] = {name: getattr(scope, name) for name in research_keys}
    old = old_stored
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
    if old and is_cross(old) and (failed := _failure(conn, d, scope)):
        # 스펙 §5-1: 정책 변경 거부의 유일한 예외 — 도중 Codex 실패 뒤 재시작은 Claude 2인으로 교체
        return _failover(
            conn, settings, d, scope, old, research=wanted["research"], reason=failed[1], now=now
        )
    if old:
        # A running session uses get(), never start(): toggles only affect subsequent starts.
        if int(old.get("version") or 1) == 1:
            # v1 진행 중 연구는 업데이트 뒤에도 해시 비교 없이 그대로 이어 간다
            return old
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


def _lane_label(lane: str) -> str:
    """사용자 문구의 판 표시: 판 a 는 비움(v1 문구 그대로), 판 b 는 「2판 」."""
    return "" if lane == "a" else "2판 "


def _missing_lane(policy: dict, lane: str) -> str | None:
    """정책에 그 판이 없으면 오류 문구. 판 a 는 `lanes()` 가 늘 만들므로 판 b 만 걸린다."""
    if lane not in {item["lane"] for item in lanes(policy)}:
        return "이 연구에는 2판이 없습니다"
    return None


def lane_error(conn, settings, d, scope, lane: str) -> str | None:
    """연구원 묶음 `--lane` 검사: a|b 형식, 그리고 판 b 는 고정 정책에 그 판이 있어야 한다.
    판 a 는 정책을 읽지 않는다(묶음의 기존 동작 유지)."""
    if lane not in ("a", "b"):
        return "--lane 은 a|b 입니다"
    if lane == "a":
        return None
    try:
        return _missing_lane(get(conn, settings, d, scope), lane)
    except ResearchPolicyError as exc:
        return str(exc)


def summary_lane_error(conn, settings, d, scope) -> str | None:
    """요약 전 심층 방어: 판 b 토론 턴 파일이 있는데 고정 정책에 판 b 가 없으면 오류
    (쓰기 검사를 거치지 않은 판 b 파일이 요약·판단에 섞이지 않게)."""
    if not any(s.startswith(scope.cross_turn_prefix) for s in list_checkpoints(d)):
        return None
    return lane_error(conn, settings, d, scope, "b")


def write_error(conn, settings, d, scope, *, round_no, side, lane="a") -> str | None:
    """Claude 가 토론 턴을 쓰기 전 검사. 판·공급자 칸·라운드 상한·입력 고정·같은 판 직전 라운드."""
    try:
        policy = get(conn, settings, d, scope)
        if err := _missing_lane(policy, lane):
            # 2판이 없는 정책(v1·꺼짐·건너뜀)에서 판 b 쓰기를 막는다(같은 모델끼리 두 판 방지)
            return err
        if not policy["enabled"]:
            return None
        if provider_for(policy, lane, side) == "codex":
            return f"Codex {side}는 전용 도구로만 기록할 수 있습니다: {scope.cli_template}"
        if round_no > policy["rounds"]:
            return f"라운드 {round_no}는 고정 정책 라운드({policy['rounds']})를 넘습니다"
        _bind(conn, d, scope, policy)
        if round_no > 0:
            for prev_side in ("bull", "bear"):
                if read_checkpoint(d, scope.turn_stage(lane, round_no - 1, prev_side)) is None:
                    return "반박 이전 라운드의 양측 턴이 필요합니다"
        return None
    except ResearchPolicyError as exc:
        return str(exc)


def rounds(conn, settings, d, scope) -> int:
    return int(get(conn, settings, d, scope)["rounds"])


def _codex_lane(policy: dict, side: str) -> str:
    """그 편이 Codex 인 판. 없으면 ResearchPolicyError."""
    for lane, slot_side in codex_slots(policy):
        if slot_side == side:
            return lane
    raise ResearchPolicyError(f"이 연구에서 Codex {side} 턴은 없습니다")


def payload(conn, settings, d, scope, round_no, *, side="bear"):
    """Codex `side` 턴 입력(그 편이 Codex 인 판의 직전 턴). 입력에 판 이름은 넣지 않는다."""
    policy = get(conn, settings, d, scope)
    return _payload(_codex_lane(policy, side), d, scope, round_no, side), policy


def _payload(lane: str, d, scope, round_no, side) -> dict:
    """`payload` 본체 — 이미 정한 판으로 입력을 만든다(정책을 두 번 읽지 않게)."""
    other = "bull" if side == "bear" else "bear"
    inputs = read_checkpoint(d, scope.input_stage) or {}
    inputs = inputs.get("data", inputs)
    # Only the previous round is included, so round0 remains independent.
    previous = {}
    if round_no:
        for prev_side in ("bull", "bear"):
            saved = read_checkpoint(d, scope.turn_stage(lane, round_no - 1, prev_side))
            if saved is None:
                raise ResearchPolicyError("반박 이전 라운드의 양측 턴이 필요합니다")
            previous[prev_side] = saved.get("data", {})
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
        "side": side,
        "reports": reports(d, scope),
        "own_previous": previous.get(side),
        "opponent_previous": previous.get(other),
        "input_context": tooling.compact_context(inputs),
    }
    try:
        return tooling.compact_debate(codex.sanitize(data))
    except ValueError as exc:
        raise ResearchPolicyError(str(exc)) from exc


def generate_turn(
    ctx, d, scope, round_no, *, side="bear", runner=None, bin_path=None, cancel=None
) -> dict:
    """Codex `side` 턴을 생성해 그 편이 Codex 인 판의 단계에 저장한다."""
    policy = None
    try:
        fixed = get(ctx.conn, ctx.settings, d, scope)
        if not fixed["enabled"]:
            return {"ok": False, "error": "Codex 토론 참여가 꺼진 연구입니다"}
        lane = _codex_lane(fixed, side)
        data = _payload(lane, d, scope, round_no, side)
        policy = fixed
        if not 0 <= round_no <= policy["rounds"]:
            return {"ok": False, "error": "고정 정책의 라운드 범위를 벗어났습니다"}
        _bind(ctx.conn, d, scope, policy)
        if isinstance(data.get("input_context"), dict):
            input_receipts.record(
                d,
                data["input_context"],
                stage=f"{scope.scope}:researcher:r{round_no}:{side}"
                + ("" if lane == "a" else f":{lane}"),
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
            validate=lambda turn: turn_error(ctx.conn, ctx.settings, d, scope, turn, lane=lane),
            repair=lambda turn: trim_evidence(turn, tooling.report_source_ids(reports(d, scope))),
        )
        stage = scope.turn_stage(lane, round_no, side)
        result["call"]["agent"] = scope.bull_agent if side == "bull" else scope.bear_agent
        invalidate_summary(d, scope)
        path = write_checkpoint(d, stage, checkpoint_payload(result["data"], result["call"]))
        return {
            "ok": True,
            "path": str(path),
            "provider": "codex",
            "round": round_no,
            "side": side,
            "lane": lane,
        }
    except codex.CodexError as exc:
        # 교차 토론(v2·두 판)의 Codex 실행 실패만 표시한다
        # — 입력 조립 실패(ResearchPolicyError)·v1 연구는 표시하지 않는다
        if policy is not None and is_cross(policy):
            mark_codex_failed(ctx.conn, d, scope, reason=str(exc), now=ctx.now())
        return {"ok": False, "error": str(exc)}
    except ResearchPolicyError as exc:
        return {"ok": False, "error": str(exc)}


def completion_error(conn, settings, d, scope) -> str | None:
    """판마다 0..rounds × 양측 턴·입력 고정·쟁점 반박·공급자 칸 증명을 확인한다.
    v1 정책은 판 a 하나라 예전과 같은 결과다."""
    try:
        policy = get(conn, settings, d, scope)
        if not policy["enabled"]:
            return None
        plan = lanes(policy)
        # Check presence before fingerprint, yielding the useful missing-turn error first.
        for item in plan:
            label = _lane_label(item["lane"])
            for rnd in range(policy["rounds"] + 1):
                for side in ("bull", "bear"):
                    if read_checkpoint(d, scope.turn_stage(item["lane"], rnd, side)) is None:
                        who = "Codex" if item[side] == "codex" else "Claude"
                        return f"필수 토론 미완료: {label}r{rnd}_{side} ({who})"
        if policy.get("context_hash") != _context_hash(d, scope):
            return "Codex 입력/리포트 증명 불일치: 새 연구로 재분석하세요"
        for item in plan:
            lane, label = item["lane"], _lane_label(item["lane"])
            for rnd in range(policy["rounds"] + 1):
                cps = {
                    side: read_checkpoint(d, scope.turn_stage(lane, rnd, side)) or {}
                    for side in ("bull", "bear")
                }
                for side in ("bull", "bear"):
                    value = cps[side].get("data") or {}
                    if err := turn_error(conn, settings, d, scope, value, lane=lane):
                        return err
                for side in ("bull", "bear"):
                    if item[side] == "codex":
                        if not _codex_proven(
                            conn, settings, d, scope, policy, cps[side], rnd, side
                        ):
                            return (
                                f"Codex 참여 증명 불일치: {label}r{rnd}_{side}, "
                                "새 연구로 재분석하세요"
                            )
                    elif (cps[side].get("call") or {}).get("provider") != "claude":
                        return f"Claude {side} 공급자 증명 없음: {label}r{rnd}"
        return None
    except ResearchPolicyError as exc:
        return f"Codex 참여 검증 실패: {exc}"
    except (ValueError, sqlite3.Error) as exc:
        return f"Codex 참여 검증 실패: {type(exc).__name__}"


def _codex_proven(conn, settings, d, scope, policy, cp, rnd, side) -> bool:
    """Codex 칸 턴이 `research_calls` 성공 행(정책·모델·그 편 PROMPT·스키마·입력·턴 해시)과
    맞는가."""
    turn = DebateTurn.model_validate(cp.get("data"))
    call = cp.get("call") or {}
    row = conn.execute(
        ("SELECT * FROM research_calls WHERE id=? AND provider='codex' AND status='ok'"),
        (call.get("call_id"),),
    ).fetchone()
    expected, _ = payload(conn, settings, d, scope, rnd, side=side)
    return not (
        turn.side != side
        or turn.round != rnd
        or row is None
        or row["policy_hash"] != policy["policy_hash"]
        or row["model"] != policy["config"]["model"]
        or row["prompt_hash"] != codex.digest(codex.PROMPTS[side])
        or row["schema_hash"] != codex.digest(codex.output_schema())
        or row["input_hash"] != codex.digest(expected)
        or row["turn_hash"] != codex.digest(cp["data"])
        or any(
            call.get(k) != row[k]
            for k in ("policy_hash", "input_hash", "prompt_hash", "schema_hash")
        )
    )


def status(conn, settings, *, now, limits: dict | None = None):
    from trader.kernel.platform import codex_bin

    enabled, source = configured(conn, settings)
    cap, cap_source = call_cap(conn, settings)
    t_enabled, t_source = throttle_enabled(conn, settings)
    # 다음 새 연구가 교차면 시작 판정(`start`)과 같은 need(Codex 자리 × (rounds+1))로 본다
    need = 1
    if enabled:
        rounds = max(1, getattr(settings, "debate_rounds", 0))
        need = codex_need({"lanes": _v2_lanes(True), "rounds": rounds})
    # 조회 화면은 codex 를 직접 실행하지 않는다 — 사용률은 호출한 쪽이 읽어 넘길 때만 본다
    t_ok, t_reason = codex_allowance(conn, settings, now=now, need=need, limits=limits, probe=False)
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
        "lanes": _v2_lanes(enabled),  # = lanes(current(...)) — status 는 debate_rounds 없이도 돈다
        "usage": {
            "provider": "codex",
            "calls": row[0],
            "errors": row[1] or 0,
            "duration_ms": row[2] or 0,
            "input_tokens": row[3],
            "output_tokens": row[4],
            "cost_usd": None,
            "subscription_utilization": None,
            "daily_call_cap": cap,
            "cap_source": cap_source,
            "default": int(cfg.daily_call_cap),
        },
        "throttle": {
            "enabled": t_enabled,
            "source": t_source,  # control(설정 화면) | yaml
            "hard_pct": float(_cfg(settings).hard_pct),
            "state": "ok" if t_ok else "over",  # over 면 새 분석은 Claude 강세·약세
            "reason": t_reason,
        },
        "last_error": last[0] if last else None,
    }


_NOTE_START = frozenset("([{:,;—–|")


def trim_evidence(value, allowed):
    """`evidence` 문자열 목록에서 「유효 source_id + 공백·괄호 등 뒤 설명」을 그 ID 로 줄인다.
    Codex 가 ID 뒤에 메모를 붙여(예: `news_macro.events 원문 그대로…`) 턴 전체가 거부되던
    문제 대응. 유효 ID 로 시작하지 않는 값은 그대로 둬 `turn_error` 가 거부하게 한다."""
    ids = sorted(set(allowed), key=len, reverse=True)  # 가장 긴 ID 우선(a.b.c > a.b)

    def fix(item):
        if not isinstance(item, str) or item in allowed:
            return item
        for sid in ids:
            rest = item[len(sid) :]
            if item.startswith(sid) and rest and (rest[0].isspace() or rest[0] in _NOTE_START):
                return sid
        return item

    if isinstance(value, dict):
        return {
            k: [fix(e) for e in v]
            if k == "evidence" and isinstance(v, list)
            else trim_evidence(v, allowed)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [trim_evidence(v, allowed) for v in value]
    return value


def turn_error(conn, settings, d, scope, value, *, lane="a"):
    """쟁점 토론 규칙 검사. 자기·상대 직전 턴은 그 판(`lane`)에서 읽는다."""
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
        allowed = tooling.report_source_ids(reports(d, scope))
        rejected = {
            source
            for item in [*issues, *turn.responses, *turn.replies]
            for source in item.evidence
        } - set(allowed)
        if rejected:
            return (
                "쟁점/반론 evidence는 실제 리포트 source_id를 사용해야 합니다 — "
                f"잘못된 값: {', '.join(sorted(rejected))} / 사용 가능: {', '.join(allowed)}"
            )
        if turn.round == 0:
            if turn.responses or turn.replies:
                return "독립 r0은 상대 responses/replies를 참조할 수 없습니다"
            return None
        if not turn.rebuttals:
            return "상호 반박 라운드 rebuttals를 비울 수 없습니다"
        other = "bear" if turn.side == "bull" else "bull"
        own = (read_checkpoint(d, scope.turn_stage(lane, turn.round - 1, turn.side)) or {}).get(
            "data"
        ) or {}
        opponent = (read_checkpoint(d, scope.turn_stage(lane, turn.round - 1, other)) or {}).get(
            "data"
        ) or {}
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
    return {
        **{k: policy[k] for k in ("enabled", "rounds", "bull", "bear")},
        "version": int(policy.get("version") or 1),
        "lanes": lanes(policy),
    }


def invalidate_summary(d: Path, research: ResearchSpec) -> bool:
    """턴 변경 전에 그 턴들의 파생 요약만 무효화한다. 결정/주문에는 손대지 않는다."""
    path = d / f"{research.summary_stage}.json"
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed
