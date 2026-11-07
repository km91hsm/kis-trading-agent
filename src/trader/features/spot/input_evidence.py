"""결정의 저장 입력과 전달용 응답 기록을 읽는다. 현재 수집·설정은 조회하지 않는다."""

from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from pathlib import Path

from trader.kernel.session import input_receipts, tooling
from trader.kernel.session.input_receipts import public_context

MAX_SNAPSHOT_BYTES = 8 * 1024 * 1024


def _read(path: Path, limit: int) -> tuple[dict, str]:
    try:
        if path.is_symlink():
            return {}, "unreadable"
        with path.open("rb") as handle:
            raw = handle.read(limit + 1)
        if len(raw) > limit:
            return {}, "too_large"
        value = json.loads(raw)
        return (value, "present") if isinstance(value, dict) else ({}, "unreadable")
    except FileNotFoundError:
        return {}, "missing"
    except (OSError, ValueError, RecursionError):
        return {}, "unreadable"


def _receipt_sections(value) -> list[dict]:
    """새 전달 기록의 원천별 상태만 읽고 옛 기록은 빈 목록으로 유지한다."""
    if not isinstance(value, list):
        return []
    result = []
    for section in value[:500]:
        if not isinstance(section, dict):
            continue
        section_id, count = section.get("id"), section.get("count")
        status = section.get("status")
        if (
            not isinstance(section_id, str)
            or not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,150}", section_id)
            or type(count) is not int
            or count < 0
            or status not in ("present", "empty", "error", "partial", "skipped")
        ):
            continue
        result.append(
            {
                "id": section_id,
                "count": count,
                "status": status,
                "source": _source_text(section, "source"),
                "received_at": _source_text(section, "received_at"),
            }
        )
    return result


def _receipt(value: dict) -> dict | None:
    """손상되거나 덧붙은 필드가 원문/임의 링크로 UI에 노출되지 않게 allowlist로 읽는다."""
    if value.get("version") != 1 or value.get("kind") != "prepared_context":
        return None
    stage = value.get("stage")
    digest = value.get("context_hash")
    stamp = value.get("recorded_at")
    if not isinstance(stage, str) or not re.fullmatch(r"[a-zA-Z0-9:_-]{1,100}", stage):
        return None
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        return None
    try:
        if (
            not isinstance(stamp, str)
            or datetime.fromisoformat(stamp.replace("Z", "+00:00")).tzinfo is None
        ):
            return None
    except ValueError:
        return None
    rows = value.get("groups")
    if not isinstance(rows, list):
        return None
    groups = []
    for group in rows[: len(input_receipts.ROOTS)]:
        if not isinstance(group, dict) or group.get("id") not in input_receipts.ROOTS:
            return None
        count, omitted = group.get("count"), group.get("identifiers_omitted")
        refs = group.get("refs")
        if (
            type(count) is not int
            or count < 0
            or type(omitted) is not int
            or not 0 <= omitted <= count
            or not isinstance(refs, list)
        ):
            return None
        safe_refs = [
            ref
            for ref in refs[:1500]
            if isinstance(ref, str)
            and (
                input_receipts.safe_url(ref)
                or re.fullmatch(
                    r"(?:news|macro|technical|fundamental|sentiment|community|blog_posts|saveticker|market_activity)[.:][a-zA-Z0-9_.:-]{1,100}",
                    ref,
                )
            )
        ]
        sources = group.get("source_counts")
        source_counts = (
            {
                key[:300]: n
                for key, n in list(sources.items())[:500]
                if isinstance(key, str) and type(n) is int and 0 <= n <= count
            }
            if isinstance(sources, dict)
            else {}
        )
        groups.append(
            {
                "id": group["id"],
                "count": count,
                "refs": safe_refs,
                "identifiers_omitted": omitted,
                "source_counts": source_counts,
                "sections": _receipt_sections(group.get("sections")),
            }
        )
    return {
        "stage": stage,
        "context_hash": digest,
        "recorded_at": stamp,
        "provider": value.get("provider") if value.get("provider") in ("claude", "codex") else None,
        "truncated": value.get("truncated") is True,
        "groups": groups,
    }


def _cited_refs(conn, dec: dict) -> set[str]:
    refs = set()
    for table in ("analyst_reports", "debate_turns"):
        rows = conn.execute(
            f"SELECT data_json FROM {table} WHERE decision_date=? AND market=? AND ticker=?",
            (dec["date"], dec["market"], dec["ticker"]),
        )
        for row in rows:
            try:
                data = json.loads(row[0])
                refs.update(
                    e["source_id"]
                    for e in data.get("evidence", [])
                    if isinstance(e, dict) and isinstance(e.get("source_id"), str)
                )
                for key in ("issues", "responses", "replies"):
                    for issue in data.get(key, []):
                        refs.update(e for e in issue.get("evidence", []) if isinstance(e, str))
            except (ValueError, TypeError, AttributeError):
                continue
    return refs


def _source_text(value: dict, key: str) -> str | None:
    text = value.get(key)
    return text[:300] if isinstance(text, str) else None


def _source_coverage(data: dict) -> list[dict]:
    coverage = data.get("source_coverage")
    if not isinstance(coverage, dict):
        return []
    rows = []
    for key, section in list(coverage.items())[:100]:
        if not isinstance(section, dict):
            continue
        children = list(section.items())[:100] if key == "macro" else [(None, section)]
        for child, value in children:
            if not isinstance(value, dict):
                continue
            count = value.get("items")
            gaps = value.get("data_gaps")
            rows.append(
                {
                    "id": f"macro:{child}" if child else str(key)[:150],
                    "status": value.get("status")
                    if value.get("status")
                    in ("present", "missing", "partial", "dropped", "unavailable", "truncated")
                    else "unrecorded",
                    "source": _source_text(value, "source"),
                    "source_asof": _source_text(value, "source_asof"),
                    "received_at": _source_text(value, "received_at"),
                    "items": count if type(count) is int and count >= 0 else None,
                    "gaps_count": len(gaps) if isinstance(gaps, list) else None,
                    "stale": value.get("stale") if type(value.get("stale")) is bool else None,
                }
            )
    return rows


def _group_refs(group: str, data: dict) -> list[str]:
    aliases = {
        "news": ["news_macro.news"],
        "macro": ["news_macro.macro"],
        "events": ["news_macro.events"],
        "community": ["sentiment.community"],
        "blog_posts": ["sentiment.community", "blog_posts"],
        "saveticker": ["sentiment.saveticker", "saveticker"],
        "market_activity": ["market_activity"],
    }
    if group in aliases:
        return aliases[group]
    value = data.get(group)
    if isinstance(value, dict) and group in {"technical", "fundamental", "sentiment"}:
        return [
            f"{group}.{key}"
            for key in value
            if re.fullmatch(r"[a-z_]{1,60}", key) and key not in {"community", "saveticker"}
        ]
    return []


def view(conn, dec: dict, state: Path) -> dict:
    root = state / "runs"
    directory = root / dec["date"] / dec["market"] / dec["ticker"]
    if root.resolve() not in directory.resolve().parents:
        directory = root / "__invalid_input_path__"
    snapshot, status = _read(directory / "00_inputs.json", MAX_SNAPSHOT_BYTES)
    data = snapshot.get("data", snapshot)
    data = data if isinstance(data, dict) else {}
    original = input_receipts.describe(public_context(data))
    try:
        reconstructed = (
            input_receipts.describe(tooling.compact_context(copy.deepcopy(data)))
            if status == "present"
            else []
        )
        reconstruction_status = "current_rules" if status == "present" else "unavailable"
    except (ValueError, TypeError, AttributeError, KeyError):
        reconstructed, reconstruction_status = [], "unavailable"
    counts = {g["id"]: g["count"] for g in reconstructed}
    refs = _cited_refs(conn, dec)
    groups, items = [], []
    for group in original:
        count = counts.get(group["id"])
        group_refs = _group_refs(group["id"], data)
        groups.append(
            {
                "id": group["id"],
                "stored_count": group["count"],
                "reconstructed_count": count,
                "omitted_count": max(0, group["count"] - count) if count is not None else None,
                "status": group["status"],
                "sections": group["sections"],
                "received_at": group["received_at"],
                "listed_count": group["listed_count"],
                "refs": group_refs,
                "cited": bool(set(group_refs) & refs),
            }
        )
        items.extend({**item, "cited": bool(set(item["refs"]) & refs)} for item in group["items"])
    receipts = []
    folder = directory / "input_receipts"
    receipt_files = sorted(folder.glob("*.json")) if not folder.is_symlink() else []
    invalid = 0
    for path in receipt_files[:100]:
        receipt, rstatus = _read(path, 256 * 1024)
        clean = _receipt(receipt) if rstatus == "present" else None
        if clean:
            receipts.append(clean)
        else:
            invalid += 1
    receipts.sort(key=lambda r: str(r.get("recorded_at", "")))
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    asof = meta.get("asof") if isinstance(meta.get("asof"), str) else None
    return {
        "decision_id": dec["id"],
        "snapshot": {"status": status, "asof": asof},
        "delivery": {
            "status": "recorded" if receipts else "unrecorded",
            "receipts": receipts,
            "unreadable_count": invalid,
            "unlisted_count": max(0, len(receipt_files) - 100),
        },
        "reconstruction": {"status": reconstruction_status},
        "groups": groups,
        "items": items,
        "source_coverage": _source_coverage(data),
    }
