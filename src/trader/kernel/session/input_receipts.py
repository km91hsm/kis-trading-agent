"""최종 공개 컨텍스트의 전달 준비 기록. 모델의 수신/활용을 증명하지 않는다.

원문은 복제하지 않고 hash와 출처 식별자만 남긴다. 이 보조 기록의 실패는 세션 결과를
바꾸지 않는다. 웹은 기존 스냅샷과 이 기록을 별도로 읽어 표시한다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from trader.kernel.platform.fsutil import atomic_replace

ROOTS = (
    "technical",
    "fundamental",
    "sentiment",
    "news",
    "macro",
    "events",
    "blog_posts",
    "community",
    "saveticker",
    "market_activity",
)
MAX_ITEMS = 500
logger = logging.getLogger("trader.session")


def safe_url(value) -> str | None:
    if not isinstance(value, str) or len(value) > 2048 or re.search(r"\s", value):
        return None
    try:
        url = urlsplit(value)
        if (
            url.scheme.lower() in {"http", "https"}
            and url.hostname
            and not url.username
            and not url.password
        ):
            return value
    except ValueError:
        pass
    return None


def _text(value, limit=300) -> str | None:
    return value[:limit] if isinstance(value, str) and value else None


def _unwrap(value):
    return value.get("data") if isinstance(value, dict) and "data" in value else value


def public_context(data: dict) -> dict:
    """원본 역할별 입력과 이미 축약된 공개 컨텍스트에 같은 항목 식별 규칙을 쓴다."""
    context = {key: data[key] for key in ROOTS if key in data}
    nm = data.get("news_macro")
    if isinstance(nm, dict):
        context.update({key: nm[key] for key in ("news", "macro", "events") if key in nm})
    sentiment = data.get("sentiment")
    if not isinstance(sentiment, dict):
        return context
    context["sentiment"] = {
        key: value for key, value in sentiment.items() if key not in {"community", "saveticker"}
    }
    community = _unwrap(sentiment.get("community"))
    posts = community.get("posts") if isinstance(community, dict) else []
    posts = posts if isinstance(posts, list) else []
    blogs = data.get("blog_posts") or [
        p
        for p in posts
        if isinstance(p, dict) and str(p.get("source", "")).startswith("naver_blog/")
    ]
    others = [
        p
        for p in posts
        if isinstance(p, dict) and not str(p.get("source", "")).startswith("naver_blog/")
    ]
    if blogs:
        context["blog_posts"] = blogs
    if others:
        context["community"] = others
    if "saveticker" in sentiment and "saveticker" not in context:
        context["saveticker"] = sentiment["saveticker"]
    if not context["sentiment"]:
        context.pop("sentiment")
    return context


def _entries(value, path, inherited=None):
    """알려진 공개 절의 항목 경계만 따라간다. 원문/알 수 없는 중첩 키는 순회하지 않는다."""
    meta = {**(inherited or {}), **(value if isinstance(value, dict) else {})}
    data = _unwrap(value)
    if isinstance(data, list):
        for i, row in enumerate(data):
            if isinstance(row, dict):
                yield f"{path}:{i}", row, meta
    elif isinstance(data, dict) and data:
        lists = [
            key
            for key in ("stock", "macro", "posts", "recent", "upcoming", "items")
            if isinstance(data.get(key), list)
        ]
        if lists:
            for key in lists:
                yield from _entries(data[key], f"{path}.{key}", meta)
        else:
            yield path, data, meta


def describe(context: dict) -> list[dict]:
    """웹용 제한된 설명. receipt는 이 중 식별자·건수만 저장한다."""
    context = public_context(context)
    groups = []
    for group in ROOTS:
        if group not in context:
            continue
        value = context[group]
        rows = []
        if group in {"macro", "technical", "fundamental", "sentiment"} and isinstance(value, dict):
            for key, item in value.items():
                if group == "sentiment" and key in {"community", "saveticker"}:
                    continue
                rows.extend(_entries(item, f"{group}:{key}"))
        else:
            rows = list(_entries(value, group))
        items = []
        for key, row, inherited in rows[:MAX_ITEMS]:
            url = safe_url(row.get("url"))
            refs = []
            source_id = _text(row.get("source_id"), 200)
            if source_id and not re.search(r"\s", source_id) and "://" not in source_id:
                refs.append(source_id)
            if group == "news" and isinstance(row.get("id"), (int, str)):
                refs.append(f"news:{str(row['id'])[:80]}")
            if group in {"macro", "technical", "fundamental", "sentiment"}:
                refs.extend([key, key.replace(":", ".", 1)])
            if url:
                refs.append(url)
            items.append(
                {
                    "id": key,
                    "group": group,
                    "refs": list(dict.fromkeys(refs)),
                    "title": _text(row.get("title")) or _text(row.get("name")) or key,
                    "source": _text(row.get("source")) or _text(inherited.get("source")),
                    "url": url,
                    "received_at": _text(row.get("received_at"))
                    or _text(inherited.get("received_at")),
                    "source_asof": _text(row.get("source_asof"))
                    or _text(row.get("posted_at"))
                    or _text(row.get("published_at"))
                    or _text(inherited.get("source_asof")),
                    "excerpt_truncated": row.get("excerpt_truncated") is True,
                }
            )
        meta = value if isinstance(value, dict) else {}
        groups.append(
            {
                "id": group,
                "count": len(rows),
                "items": items,
                "status": "error" if meta.get("error") else "present" if rows else "empty",
                "received_at": _text(meta.get("received_at")),
                "listed_count": len(items),
            }
        )
    return groups


def record(
    directory: Path, context: dict, *, stage: str, provider: str | None, recorded_at: str
) -> bool:
    """호출 직전/도구 응답 생성 직후의 컨텍스트를 변경하지 않고 기록한다."""
    try:
        canonical = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        groups = [
            {
                "id": g["id"],
                "count": g["count"],
                "refs": list(dict.fromkeys(ref for item in g["items"] for ref in item["refs"])),
                "identifiers_omitted": g["count"] - g["listed_count"],
                "source_counts": dict(
                    Counter(item["source"] or "unrecorded" for item in g["items"])
                ),
            }
            for g in describe(context)
        ]
        receipt = {
            "version": 1,
            "kind": "prepared_context",
            "stage": stage,
            "provider": provider if provider in {"claude", "codex"} else None,
            "recorded_at": recorded_at,
            "context_hash": hashlib.sha256(canonical.encode()).hexdigest(),
            "groups": groups,
            "truncated": (context.get("truncation") or {}).get("truncated") is True,
        }
        folder = directory / "input_receipts"
        if folder.is_symlink():
            raise OSError("receipt directory is a symlink")
        folder.mkdir(parents=True, exist_ok=True)
        name = uuid4().hex
        temp, final = folder / f".{name}.tmp", folder / f"{name}.json"
        temp.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
        atomic_replace(temp, final)
        return True
    except Exception:  # noqa: BLE001 - 보조 감사 기록은 분석/주문 성공 여부를 바꾸지 않는다
        logger.warning("input_receipt_unrecorded: stage=%s", stage)
        return False
