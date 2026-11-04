"""세션 도구(`trader tool …`) 공용 헬퍼 — 체크포인트 읽기·토론 턴 묶기·묶음 크기 줄이기.

기능 이름을 모른다: 체크포인트 이름 규약은 패턴 인자로 받는다(기본은 `20_debate_r<n>_<side>`).
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from trader.kernel.session.debate import research_summary, signal_direction
from trader.kernel.session.run_store import (
    checkpoint_payload,
    list_checkpoints,
    read_checkpoint,
    write_checkpoint,
)
from trader.kernel.session.schemas import CallMeta, DebateTurn

DEBATE_RE = re.compile(r"^20_debate_r(\d+)_(bull|bear)$")
ANALYST_RE = re.compile(r"^10_analyst_([a-z_]+)$")

# 트레이더 묶음에서 리포트당 남기는 키(evidence 는 뗀다)
TRADER_REPORT_KEYS = (
    "ticker",
    "asof",
    "role",
    "summary",
    "bullets",
    "signal",
    "score",
    "confidence",
    "horizon",
    "key_risks",
    "counter_evidence",
    "data_gaps",
)

EVIDENCE_ROLES = ("technical", "fundamental", "news_macro", "sentiment")
EVIDENCE_DEFINITION = (
    "리포트가 낸 근거 항목 수(evidence)·고유 출처 수(source_id)·data_gaps·counter_evidence·"
    "key_risks 개수와 신호 방향. 파이썬이 센 개수일 뿐 검증된 근거 수가 아니다 — 중복·상충 "
    "근거도 그대로 센다"
)


def unescape(obj: Any) -> Any:
    """Claude Code 가 서브에이전트 출력의 <·>·& 를 이스케이프하므로 문자열 값을 되돌린다."""
    if isinstance(obj, str):
        return html.unescape(obj)
    if isinstance(obj, dict):
        return {k: unescape(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [unescape(v) for v in obj]
    return obj


def call_meta(agent: str | None, meta: dict | None) -> dict | None:
    """체크포인트 `call` 필드. 둘 다 없으면 None, `CallMeta` 에 없는 키는 버린다."""
    if agent is None and not meta:
        return None
    base = {"agent": agent or "unknown"}
    base.update({k: v for k, v in (meta or {}).items() if k in CallMeta.model_fields})
    return CallMeta.model_validate(base).model_dump()


def load_turns(d: Path, pattern: re.Pattern[str] = DEBATE_RE) -> dict[int, dict[str, DebateTurn]]:
    """토론 턴 체크포인트를 {round: {side: DebateTurn}} 로. 패턴의 그룹 1 = 라운드, 2 = 측."""
    turns: dict[int, dict[str, DebateTurn]] = {}
    for stage in list_checkpoints(d):
        m = pattern.match(stage)
        if not m:
            continue
        data = read_checkpoint(d, stage) or {}
        turns.setdefault(int(m.group(1)), {})[m.group(2)] = DebateTurn.model_validate(
            data["data"]
        )
    return turns


def schema_error(model: type, e: ValidationError) -> dict:
    """pydantic 검증 실패 → `{"ok": False, "error": "스키마 검증 실패(<모델>): <loc>: <msg>"}`
    (항목은 `; ` 로 잇는다)."""
    msgs = "; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors())
    return {"ok": False, "error": f"스키마 검증 실패({model.__name__}): {msgs}"}


def validate_payload(model: type, payload: dict) -> tuple[Any, dict | None]:
    """pydantic 검증(HTML 엔티티 되돌림 포함). 실패면 (None, {"ok": False, "error"})."""
    try:
        return model.model_validate(unescape(payload)), None  # type: ignore[attr-defined]
    except ValidationError as e:
        return None, schema_error(model, e)


def summarize_turns(d: Path, out_stage: str, pattern: re.Pattern[str]) -> dict:
    """토론 턴 → 연구 요약(수렴 감시 포함) 체크포인트(시장·파생 세션 공용, 패턴만 다르다)."""
    turns = load_turns(d, pattern)
    if 0 not in turns or set(turns[0]) != {"bull", "bear"}:
        return {"ok": False, "error": "라운드 0 의 bull·bear 턴이 모두 필요합니다"}
    final_round = max(r for r, sides in turns.items() if set(sides) == {"bull", "bear"})
    try:
        summ = research_summary(turns[0], turns[final_round], rounds=final_round)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    path = write_checkpoint(d, out_stage, checkpoint_payload(summ, None))
    return {
        "ok": True, "path": str(path), "rounds": final_round,
        "sycophantic": summ["sycophantic"], "preferred": summ["preferred"],
    }  # fmt: skip


def reports(d: Path) -> list[dict]:
    """`10_analyst_<role>` 체크포인트의 data(dict 인 것만)."""
    out = []
    for stage in list_checkpoints(d):
        if ANALYST_RE.match(stage):
            data = read_checkpoint(d, stage) or {}
            if isinstance(data.get("data"), dict):
                out.append(data["data"])
    return out


def _size(obj: Any) -> int:
    return len(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def _cut(v: Any, n: int) -> Any:
    if isinstance(v, str):
        return v if len(v) <= n else v[: n - 1] + "…"
    if isinstance(v, list):
        return [_cut(x, n) for x in v]
    if isinstance(v, dict):
        return {k: _cut(x, n) for k, x in v.items()}
    return v


def _trim_lists(v: Any, keep: int) -> Any:
    """`keep` 보다 긴 dict 목록은 마지막 `keep` 건만(시계열은 오름차순이라 꼬리가 최신)."""
    if isinstance(v, list):
        if len(v) > keep and all(isinstance(x, dict) for x in v):
            v = v[-keep:]
        return [_trim_lists(x, keep) for x in v]
    if isinstance(v, dict):
        return {k: _trim_lists(x, keep) for k, x in v.items()}
    return v


def shrink(obj: dict, limit: int, *, drop_evidence: bool = False) -> dict:
    """UTF-8 바이트 한도를 넘으면 리포트의 evidence → 긴 문자열 순으로 줄인다."""
    if drop_evidence and "reports" in obj:
        obj = {
            **obj,
            "reports": [{k: r[k] for k in TRADER_REPORT_KEYS if k in r} for r in obj["reports"]],
        }
    if _size(obj) <= limit:
        return obj
    if "reports" in obj and any("evidence" in r for r in obj["reports"]):
        return shrink(obj, limit, drop_evidence=True)
    # 긴 목록(일봉·분봉 같은 시계열, 오름차순)은 꼬리(최신)만 남긴다 — 문자열을 자르기 전에
    for keep in (120, 60, 30):
        trimmed = _trim_lists(obj, keep)
        if _size(trimmed) <= limit:
            note = f"묶음이 {limit}바이트를 넘어 긴 목록을 최근 {keep}건으로 줄였습니다"
            return {**trimmed, "note": note}
    obj = trimmed
    for n in (300, 200, 140, 100):
        cut = {
            **_cut(obj, n),
            "note": f"묶음이 {limit}바이트를 넘어 긴 문자열을 {n}자에서 잘랐습니다",
        }
        if _size(cut) <= limit:
            return cut
    return cut


def evidence_stats(reports: list[dict]) -> dict:
    """트레이더 묶음이 evidence 를 떼기 전에 원본 리포트에서 센 숫자. 프롬프트의 '검증 가능한
    근거 수' 판단은 이 숫자와 리포트 본문(bullets·summary)을 함께 본다."""
    roles: dict[str, dict] = {}
    for r in reports:
        ev = r.get("evidence") or []
        roles[str(r.get("role"))] = {
            "evidence": len(ev),
            "sources": len({e.get("source_id") for e in ev if isinstance(e, dict)}),
            "data_gaps": len(r.get("data_gaps") or []),
            "counter_evidence": len(r.get("counter_evidence") or []),
            "key_risks": len(r.get("key_risks") or []),
            "direction": signal_direction(r.get("signal")),
        }
    directions: dict[str, int] = {}
    for v in roles.values():
        directions[v["direction"]] = directions.get(v["direction"], 0) + 1
    top = max((n for k, n in directions.items() if k != "NEUTRAL"), default=0)
    return {
        "definition": EVIDENCE_DEFINITION,
        "roles": roles,
        "missing_roles": [x for x in EVIDENCE_ROLES if x not in roles],
        "directions": directions,
        # 방향 일치도 = 가장 많은 방향(중립 제외)의 역할 수 ÷ 받은 리포트 수
        "agreement": round(top / len(roles), 4) if roles else 0.0,
    }


def brief_turn(t: dict) -> dict:
    """우선순위가 아닌 쪽 라운드의 턴은 핵심만 남긴다 (트레이더 묶음 크기 절감)."""
    return {
        k: t.get(k) for k in ("side", "round", "stance", "valuation_view", "timing", "conviction")
    }


def trader_summary(summ: dict) -> dict:
    """preferred 쪽 턴은 전문, 반대쪽은 핵심만 (트레이더 md 가 preferred 를 우선한다)."""
    if summ.get("skipped_debate"):
        return dict(summ)  # 토론 생략(합의) 요약은 턴이 없어 줄일 것이 없다
    out = dict(summ)
    other = "final" if summ.get("preferred") == "round0" else "round0"
    if isinstance(summ.get(other), dict):
        out[other] = {
            side: brief_turn(t) for side, t in summ[other].items() if isinstance(t, dict)
        }
    # issues 는 final 양측 rebuttals 의 복제라 트레이더 묶음에선 뺀다
    out.pop("issues", None)
    out["note"] = f"{other} 턴은 핵심만, issues 는 final.rebuttals 와 같아 생략"
    return out
