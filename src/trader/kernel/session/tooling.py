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

from trader.kernel.session.debate import merge_lanes, research_summary, signal_direction
from trader.kernel.session.run_store import (
    checkpoint_payload,
    list_checkpoints,
    read_checkpoint,
    write_checkpoint,
)
from trader.kernel.session.schemas import CallMeta, DebateTurn


def debate_re(turn_prefix: str, cross_turn_prefix: str) -> re.Pattern[str]:
    """토론 턴 단계 정규식. 이름 그룹 `b`(판 B 접두사면 매치)·`round`·`side`."""
    return re.compile(
        rf"^(?:(?P<b>{re.escape(cross_turn_prefix)})|{re.escape(turn_prefix)})"
        r"(?P<round>\d+)_(?P<side>bull|bear)$"
    )


# 기본값: 커널 docstring 의 기존 규약(spot 이름)
DEBATE_RE = debate_re("20_debate_r", "20_debatex_r")
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


def parse_debate_stage(pattern: re.Pattern[str], stage: str) -> tuple[str, int, str] | None:
    """단계 이름 → `(판, 라운드, 측)`. 맞지 않으면 None. `debate_re` 의 이름 그룹이 없는
    패턴(그룹 1 = 라운드, 2 = 측)은 판 "a" 로 본다."""
    m = pattern.match(stage)
    if not m:
        return None
    if "round" not in pattern.groupindex:
        return "a", int(m.group(1)), m.group(2)
    return ("b" if m.group("b") else "a"), int(m.group("round")), m.group("side")


def load_lanes(
    d: Path, pattern: re.Pattern[str]
) -> dict[str, dict[int, dict[str, DebateTurn]]]:
    """토론 턴 체크포인트를 {판: {round: {side: DebateTurn}}} 로. 턴이 있는 판만 키로 둔다."""
    lanes: dict[str, dict[int, dict[str, DebateTurn]]] = {}
    for stage in list_checkpoints(d):
        parsed = parse_debate_stage(pattern, stage)
        if parsed is None:
            continue
        lane, rnd, side = parsed
        data = read_checkpoint(d, stage) or {}
        lanes.setdefault(lane, {}).setdefault(rnd, {})[side] = DebateTurn.model_validate(
            data["data"]
        )
    return lanes


def load_turns(
    d: Path, pattern: re.Pattern[str] = DEBATE_RE, *, lane: str = "a"
) -> dict[int, dict[str, DebateTurn]]:
    """토론 턴 체크포인트를 {round: {side: DebateTurn}} 로(한 판만, 기본 판 a)."""
    return load_lanes(d, pattern).get(lane, {})


def previous_turns(
    d: Path, pattern: re.Pattern[str], *, lane: str, round_no: int, side: str
) -> tuple[DebateTurn | None, DebateTurn | None]:
    """연구원 묶음의 (자기, 상대) 직전 턴 — 같은 판(`lane`)의 `round_no - 1` 에서만 읽는다.

    라운드 0 은 (None, None). 직전 라운드 양측 중 하나라도 없으면 `ValueError`."""
    if round_no <= 0:
        return None, None
    prev = load_turns(d, pattern, lane=lane).get(round_no - 1, {})
    own = prev.get(side)
    opp = prev.get("bear" if side == "bull" else "bull")
    if own is None or opp is None:
        raise ValueError(f"라운드 {round_no - 1} 의 양측 턴이 모두 필요합니다")
    return own, opp


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


_R0_REQUIRED = "라운드 0 의 bull·bear 턴이 모두 필요합니다"


def summarize_lanes(
    lanes: dict[str, dict[int, dict[str, DebateTurn]]],
    *,
    evidence_ids: set[str] | None = None,
) -> dict:
    """판별 토론 턴 → 판마다 `research_summary`(양측이 있는 마지막 라운드) → `merge_lanes`.

    판 a 가 없거나 어느 판이든 라운드 0 양측이 없으면 `ValueError`(판 b 는 앞에 「2판: 」)."""
    if "a" not in lanes:
        raise ValueError(_R0_REQUIRED)
    summaries: dict[str, dict] = {}
    for lane in sorted(lanes):
        turns = lanes[lane]
        if 0 not in turns or set(turns[0]) != {"bull", "bear"}:
            raise ValueError(_R0_REQUIRED if lane == "a" else f"2판: {_R0_REQUIRED}")
        final_round = max(r for r, sides in turns.items() if set(sides) == {"bull", "bear"})
        summaries[lane] = research_summary(
            turns[0], turns[final_round], rounds=final_round, evidence_ids=evidence_ids
        )
    return merge_lanes(summaries, evidence_ids=evidence_ids)


def summarize_turns(d: Path, out_stage: str, pattern: re.Pattern[str]) -> dict:
    """토론 턴 → 연구 요약(수렴 감시 포함) 체크포인트(시장·파생 세션 공용, 패턴만 다르다).
    두 판이면 같은 편을 합친 요약에 판별 원본 `by_lane` 이 붙는다."""
    lanes = load_lanes(d, pattern)
    try:
        summ = summarize_lanes(lanes)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    path = write_checkpoint(d, out_stage, checkpoint_payload(summ, None))
    return {
        "ok": True, "path": str(path), "rounds": summ["rounds"],
        "sycophantic": summ["sycophantic"], "preferred": summ["preferred"],
        "lanes": sorted(lanes),
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


# Only these documented ascending series retain their tail. News/rankings/unknown lists
# retain their head; arbitrary list ordering is never inferred from dict-valued rows.
_ASCENDING_SERIES = frozenset({"daily", "minute", "candles", "bars", "closes"})
_QUALITY_KEYS = frozenset(
    {
        "meta",
        "source",
        "source_asof",
        "asof",
        "received_at",
        "observed_at",
        "generated_at",
        "market",
        "currency",
        "scope",
        "basis",
        "status",
        "stale",
        "coverage",
        "source_coverage",
        "streams_status",
        "data_gaps",
        "fallback_reason",
        "errors",
        "error",
        "truncation",
        "note",
    }
)


_IDENTITY_FIELDS = {
    "url",
    "source",
    "requested_source",
    "source_asof",
    "asof",
    "received_at",
    "observed_at",
    "observed_date",
    "unit",
    "timestamp_precision",
    "session",
    "generated_at",
    "market",
    "currency",
    "scope",
    "basis",
    "status",
    "series_id",
    "instrument",
    "ticker",
    "date",
    "posted_at",
    "value",
    "price",
    "turnover",
    "turnover_unit",
    "turnover_basis",
    "turnover_source_field",
    "recent_5m",
    "previous_5m",
}


# 축약해도 값을 그대로 두는 키 — 요약의 판 목록(`lanes`)은 잘리면 판 b 존재를 잃는다
_PROTECTED_KEYS = frozenset({"lanes"})


def _compact(
    v: Any,
    keep: int,
    chars: int | None,
    path: tuple[str, ...] = (),
    *,
    mapping_limit: int | None = None,
) -> Any:
    if isinstance(v, str):
        if chars is not None and path[-1:] in (
            ("data_gaps",),
            ("error",),
            ("fallback_reason",),
        ):
            return _cut(v, max(chars, 300))
        return (
            _cut(v, chars)
            if chars is not None and (not path or path[-1] not in _IDENTITY_FIELDS)
            else v
        )
    if isinstance(v, list):
        tail = any(k in _ASCENDING_SERIES for k in path[-2:])
        selected = v[-keep:] if tail else v[:keep]
        return [_compact(x, keep, chars, path, mapping_limit=mapping_limit) for x in selected]
    if isinstance(v, dict):
        keys = list(v)
        if mapping_limit is not None and "source_coverage" not in path:
            kept = _QUALITY_KEYS | _PROTECTED_KEYS
            priority = [k for k in v if k in kept]
            ordinary = [k for k in v if k not in kept][:mapping_limit]
            keys = priority + ordinary
        return {
            k: v[k]
            if k in _PROTECTED_KEYS
            else _compact(v[k], keep, chars, (*path, k), mapping_limit=mapping_limit)
            for k in keys
        }
    return v


def _delivery_coverage(value: Any) -> Any:
    """중첩 context를 포함해 실제 전달된 근거에 맞춰 coverage를 계산한다."""
    if isinstance(value, list):
        return [_delivery_coverage(v) for v in value]
    if not isinstance(value, dict):
        return value
    result = {k: _delivery_coverage(v) for k, v in value.items()}
    coverage = result.get("source_coverage")
    if not isinstance(coverage, dict):
        return result
    macro = result.get("macro") or (result.get("news_macro") or {}).get("macro") or {}
    for key, details in (coverage.get("macro") or {}).items():
        if isinstance(details, dict) and details.get("status") in ("present", "partial"):
            observation = (macro.get(key) or {}).get("data")
            if not isinstance(observation, dict) or observation.get("value") is None:
                details["status"] = "dropped"
                details["data_gaps"] = [
                    *(details.get("data_gaps") or []),
                    "observation not included in delivered context",
                ]
    for source, details in coverage.items():
        if not source.startswith("naver_blog/") or not isinstance(details, dict):
            continue
        if details.get("status") not in ("present", "partial"):
            continue
        community = ((result.get("sentiment") or {}).get("community") or {}).get("data") or {}
        posts = result.get("blog_posts") or community.get("posts") or []
        actual = sum(
            1
            for p in posts
            if isinstance(p, dict)
            and p.get("source") == source
            and any(str(p.get(k) or "").strip(" …") for k in ("body", "excerpt"))
        )
        original = details.get("original_items", details.get("items", actual))
        if isinstance(original, int) and actual < original:
            details.update(
                status="partial" if actual else "dropped", items=actual, original_items=original
            )
            gap = "some blog evidence omitted from delivered context"
            details["data_gaps"] = list(dict.fromkeys([*(details.get("data_gaps") or []), gap]))
    return result


def shrink(obj: dict, limit: int, *, drop_evidence: bool = False) -> dict:
    """축약 기록을 포함한 전체 UTF-8 직렬화 결과가 바이트 상한을 지키게 한다."""
    if limit < 2:
        raise ValueError("JSON dict requires at least 2 bytes")
    if drop_evidence and "reports" in obj:
        obj = {
            **obj,
            "reports": [{k: r[k] for k in TRADER_REPORT_KEYS if k in r} for r in obj["reports"]],
        }
    if _size(obj) <= limit:
        return obj
    if "reports" in obj and any("evidence" in r for r in obj["reports"]):
        reduced = shrink(obj, limit, drop_evidence=True)
        reduced["truncation"] = {
            **(reduced.get("truncation") or {}),
            "truncated": True,
            "evidence_dropped": True,
            "original_bytes": _size(obj),
            "limit_bytes": limit,
        }
        return shrink(reduced, limit)
    original_bytes = _size(obj)
    metadata = {
        "note": "입력 일부 축약; 뉴스·순위·순서미상 목록은 앞, 오름차순 봉은 뒤 보존",
        "truncation": {
            "truncated": True,
            "original_bytes": original_bytes,
            "limit_bytes": limit,
            "list_policy": "head; ascending candles tail",
        },
    }
    for chars in (None, 300, 140, 64, 16, 1):
        for keep in (120, 60, 30, 10, 3, 1):
            compact = _delivery_coverage({**_compact(obj, keep, chars), **metadata})
            if _size(compact) <= limit:
                return compact
    # Only after text and list reduction may pathological mappings lose keys.
    for mapping_limit in (120, 30, 3, 1):
        compact = _delivery_coverage(
            {**_compact(obj, 1, 1, mapping_limit=mapping_limit), **metadata}
        )
        if _size(compact) <= limit:
            return compact
    # Pathological objects can contain arbitrarily many quality keys at every level.
    # Preserve a small top-level quality summary before the final tiny-budget fallback.
    quality = {k: v for k, v in obj.items() if k in _QUALITY_KEYS}
    for chars in (64, 16, 1):
        compact = _delivery_coverage({**_compact(quality, 1, chars), **metadata})
        if _size(compact) <= limit:
            return compact
    for compact in ({"truncation": {"truncated": True}}, {}):
        if _size(compact) <= limit:
            return compact
    return {}


_DEBATE_PROSE = frozenset(
    {
        "summary",
        "bullets",
        "key_risks",
        "counter_evidence",
        "data_gaps",
        "claim",
        "topic",
        "response",
        "arguments",
        "rebuttals",
        "what_would_change_my_mind",
        "reason",
        "condition",
        "price_zone",
        "excerpt",
        "body",
    }
)


def _debate_prose(value: Any, chars: int, field: str = "") -> Any:
    """근거·쟁점·응답 목록과 식별자를 그대로 두고 설명문만 줄인다."""
    if isinstance(value, dict):
        return {key: _debate_prose(item, chars, key) for key, item in value.items()}
    if isinstance(value, list):
        return [_debate_prose(item, chars, field) for item in value]
    if isinstance(value, str) and field in _DEBATE_PROSE:
        return _cut(value, chars)
    return value


def compact_debate(obj: dict, limit: int = 24_000) -> dict:
    """양 모델의 토론 입력을 같은 규칙으로 제한한다. 필수 식별자는 생략하지 않는다."""
    original = _size(obj)
    if original <= limit:
        return obj
    protected = {"reports", "own_previous", "opponent_previous", "research_policy"}
    metadata = {
        "truncated": True,
        "original_bytes": original,
        "limit_bytes": limit,
        "evidence_dropped": False,
        "note": "설명문·보조 context 일부 축약; 모든 근거·쟁점·응답 식별자와 항목 보존",
    }
    for chars in (300, 140, 64):
        reduced = {
            key: _debate_prose(value, chars) if key in protected else value
            for key, value in obj.items()
        }
        for context_limit in (8000, 4000, 2000, 500):
            compact = {**reduced, "truncation": metadata}
            for key in ("input_context", "regime", "market_view", "index"):
                if isinstance(compact.get(key), dict):
                    compact[key] = shrink(compact[key], context_limit)
            if _size(compact) <= limit:
                return compact
    raise ValueError(f"토론 필수 근거·쟁점 구조가 {limit:,} UTF-8 bytes 상한을 넘습니다")


def debate_bundle(obj: dict, limit: int = 24_000) -> dict:
    """Claude 도구 경계에서도 필수 구조 초과를 자료 없는 성공으로 숨기지 않는다."""
    try:
        return {"ok": True, "data": compact_debate(obj, limit)}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}


def compact_context(inputs: dict) -> dict:
    """계좌·포트폴리오·주문과 분리된 공개 연구 근거를 제한된 크기로 조립한다."""
    nm = inputs.get("news_macro") or {}
    sentiment = inputs.get("sentiment") or {}
    data = {
        k: inputs[k]
        for k in (
            "meta",
            "market",
            "asof",
            "scope",
            "market_activity",
            "data_gaps",
            "source_coverage",
            "truncation",
        )
        if k in inputs
    }
    for key, value in (
        ("news", nm.get("news") or inputs.get("macro_news") or inputs.get("news")),
        ("macro", nm.get("macro") or inputs.get("macro")),
        ("events", nm.get("events")),
        ("saveticker", inputs.get("saveticker") or sentiment.get("saveticker")),
    ):
        if value is not None:
            data[key] = value
    # FO stores news_macro itself as a Section; spot uses per-item Sections.
    if {"data", "received_at", "error"} <= set(nm):
        section_data = nm.get("data") or {}
        data["news"] = {**nm, "data": section_data.get("news")}
        if section_data.get("macro"):
            data["macro"] = section_data["macro"]
    if {"data", "received_at", "error"} <= set(sentiment):
        sentiment = sentiment.get("data") or {}
    community = (sentiment.get("community") or {}).get("data") or {}
    blogs = inputs.get("blog_posts") or [
        p
        for p in community.get("posts") or []
        if str(p.get("source") or "").startswith("naver_blog/")
    ]
    if blogs:
        data["blog_posts"] = blogs
    # 긴 첫 글만 남기기 전에 최근 글들의 발췌를 함께 담는다.
    # 이 단계도 뉴스·매크로·출처를 함께 계산해 전체 byte 상한을 지킨다.
    if isinstance(blogs, list) and blogs and _size(data) > 8000:
        for chars in (240, 140, 64):
            candidate = _compact(data, 5, chars)
            for original, post in zip(blogs, candidate["blog_posts"], strict=False):
                if not isinstance(post, dict):
                    continue
                if "title" in post:
                    post["title"] = _cut(post["title"], 160)
                post["excerpt_truncated"] = bool(original.get("excerpt_truncated")) or any(
                    isinstance(original.get(key), str) and len(original[key]) > chars
                    for key in ("body", "excerpt")
                )
            candidate["truncation"] = {
                **(data.get("truncation") or {}),
                "truncated": True,
                "original_bytes": _size(data),
                "limit_bytes": 8000,
                "list_policy": "head; ascending candles tail",
                "blog_policy": "recent five source excerpts before dropping posts",
            }
            candidate = _delivery_coverage(candidate)
            if _size(candidate) <= 8000:
                return candidate
        # 글을 버리기 전에 다른 긴 목록을 줄여 다섯 발췌의 공간을 확보한다.
        while True:
            for keep in (5, 3, 1):
                reduced = _compact(candidate, keep, None)
                reduced["blog_posts"] = candidate["blog_posts"]
                reduced = _delivery_coverage(reduced)
                if _size(reduced) <= 8000:
                    return reduced
            if not candidate["blog_posts"]:
                break
            # 그래도 안 들어가면 큰 식별자부터 제외한다. 동률이면 뒤의 글을 뺀다.
            largest = max(
                range(len(candidate["blog_posts"])),
                key=lambda i: (_size(candidate["blog_posts"][i]), i),
            )
            candidate["blog_posts"].pop(largest)
        # 일반 축약으로 넘어가도 이미 제외한 큰 식별자를 다시 넣지 않는다.
        data["blog_posts"] = candidate["blog_posts"]
    result = shrink(_delivery_coverage(data), 8000)
    if isinstance(blogs, list) and isinstance(result.get("blog_posts"), list):
        # 극단적인 크기로 키까지 생략된 글은 출처 없는 근거로 전달하지 않는다.
        identity = ("url", "source", "posted_at", "received_at")
        result["blog_posts"] = [
            post for post in result["blog_posts"]
            if isinstance(post, dict) and any(
                isinstance(original, dict)
                and all(post.get(key) == original.get(key) for key in identity)
                for original in blogs
            )
        ]
        result = shrink(_delivery_coverage(result), 8000)
    return result


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


def _brief_lane(s: dict) -> dict:
    """판별 요약 → round0·final 턴은 `brief_turn`, issues 는 뺀 사본."""
    out = {k: v for k, v in s.items() if k != "issues"}
    for part in ("round0", "final"):
        if isinstance(s.get(part), dict):
            out[part] = {side: brief_turn(t) for side, t in s[part].items() if isinstance(t, dict)}
    return out


def trader_summary(summ: dict) -> dict:
    """preferred 쪽 턴은 전문, 반대쪽은 핵심만 (트레이더 md 가 preferred 를 우선한다)."""
    if summ.get("skipped_debate"):
        return dict(summ)  # 토론 생략(합의) 요약은 턴이 없어 줄일 것이 없다
    out = dict(summ)
    other = "final" if summ.get("preferred") == "round0" else "round0"
    if isinstance(summ.get(other), dict):
        out[other] = {side: brief_turn(t) for side, t in summ[other].items() if isinstance(t, dict)}
    # issues 는 final 양측 rebuttals 의 복제라 트레이더 묶음에선 뺀다
    out.pop("issues", None)
    out["note"] = f"{other} 턴은 핵심만, issues 는 final.rebuttals 와 같아 생략"
    if isinstance(summ.get("by_lane"), dict):
        # 판별 원본은 대조용 — 턴은 핵심만, issues 는 뺀다(24KB 묶음 안에 들도록)
        out["by_lane"] = {
            lane: _brief_lane(s) for lane, s in summ["by_lane"].items() if isinstance(s, dict)
        }
        out["note"] += "; by_lane 의 판별 턴도 핵심만"
    return out
