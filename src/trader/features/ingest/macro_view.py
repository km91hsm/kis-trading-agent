"""저장된 00_inputs의 매크로 관측값 조회. 수집·설정·키·원장 쓰기를 하지 않는다."""

from __future__ import annotations

import json
import math
import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from itertools import islice
from pathlib import Path
from typing import Any

from trader.kernel.config.time import KST

LOOKBACK_DAYS = 31
MAX_FILES = 200
MAX_ENTRIES = 2000
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
# 이 읽기 모델이 인식하는 실제 저장 상품. 키가 같아도 다른 상품의 값은 섞지 않는다.
INSTRUMENTS = {
    "sp500": ("S&P 500", "S&P 500", "pt", "SP500", "SPX", None),
    "nasdaq": ("나스닥 종합", "NASDAQ Composite", "pt", "NASDAQCOM", "COMP", None),
    "us10y": (
        "미국채 10년",
        "US Treasury 10-year constant maturity yield",
        "%",
        "DGS10",
        None,
        None,
    ),
    "wti": (
        "WTI 현물",
        "WTI crude oil spot price",
        "USD/배럴",
        "DCOILWTICO",
        None,
        "WTI 현물 가격 · WTI 선물 대체",
    ),
    "dxy": (
        "광의 달러지수",
        "Federal Reserve broad US dollar index",
        "2006-01=100",
        "DTWEXBGS",
        None,
        "연준 광의 달러지수 · ICE DXY 대체",
    ),
}


def _timestamp(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return value.astimezone(UTC) if value.tzinfo is not None else None
    except ValueError:
        return None


def _number(raw: Any) -> Decimal | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)) or len(str(raw)) > 64:
        return None
    try:
        value = Decimal(str(raw))
        return value if value.is_finite() and math.isfinite(float(value)) else None
    except (InvalidOperation, ValueError, OverflowError):
        return None


def _empty(key: str) -> dict:
    label, instrument, unit, _, _, proxy_reason = INSTRUMENTS[key]
    return {
        "key": key,
        "label": label,
        "instrument": instrument,
        "unit": unit,
        "value": None,
        "status": "unavailable",
        "reason": "저장된 유효 관측값 없음",
        "source": None,
        "series_id": None,
        "source_metadata": None,
        "proxy": proxy_reason is not None,
        "proxy_reason": proxy_reason,
        "observed_date": None,
        "observed_at": None,
        "received_at": None,
        "stored_at": None,
        "stored_at_basis": "file_modified_at",
        "change_pct": None,
        "change_reason": "원천 등락률 미제공 · 저장값 간 임의 계산 안 함",
        "live_verified": False,
        "freshness": {
            "status": "unavailable",
            "observation_age_days": None,
            "reason": "저장된 유효 관측값 없음",
        },
    }


def _observation(section: dict, *, now: datetime) -> tuple[date, str | None] | None:
    data = section["data"]
    raw = (
        data.get("asof")
        or data.get("date")
        or section.get("observed_at")
        or section.get("observed_date")
    )
    if not isinstance(raw, str):
        return None
    observed_at = None
    try:
        if len(raw) == 10:
            day = date.fromisoformat(raw)
        else:
            stamp = _timestamp(raw)
            if stamp is None or stamp > now:
                return None
            day, observed_at = stamp.date(), stamp.isoformat()
    except ValueError:
        return None
    if day > now.date():
        return None
    # 중복 메타데이터 중 하나라도 미래/손상 시각이면 해당 관측은 채택하지 않는다.
    for stamp_raw in (
        data.get("asof"),
        data.get("date"),
        section.get("source_asof"),
        section.get("observed_date"),
        section.get("observed_at"),
    ):
        if stamp_raw is None:
            continue
        if not isinstance(stamp_raw, str):
            return None
        try:
            if len(stamp_raw) == 10:
                if date.fromisoformat(stamp_raw) > now.date():
                    return None
            else:
                stamp = _timestamp(stamp_raw)
                if stamp is None or stamp > now:
                    return None
        except ValueError:
            return None
    if section.get("received_at") is not None:
        stamp = _timestamp(section["received_at"])
        if stamp is None or stamp > now:
            return None
    return day, observed_at


def _item(key: str, section: Any, saved: datetime, now: datetime) -> dict | None:
    if (
        not isinstance(section, dict)
        or section.get("error")
        or not isinstance(section.get("data"), dict)
    ):
        return None
    data = section["data"]
    value = _number(data.get("value"))
    observation = _observation(section, now=now)
    if value is None or observation is None:
        return None
    _, instrument, _, fred, kis, _ = INSTRUMENTS[key]
    source = section.get("source") or "FRED"
    expected = fred if source == "FRED" else kis if source == "KIS" else None
    series = section.get("series_id") or expected
    if expected is None or series != expected:
        return None
    if section.get("instrument") not in (None, instrument):
        return None
    if data.get("symbol") not in (None, series):
        return None
    day, observed_at = observation
    change = _number(data.get("change_pct"))
    out = _empty(key)
    out.update(
        {
            "value": str(value),
            "status": "available",
            "reason": None,
            "source": source,
            "series_id": series,
            "source_metadata": "recorded"
            if section.get("source") and section.get("series_id")
            else "legacy_mapping",
            "observed_date": day.isoformat(),
            "observed_at": observed_at,
            "received_at": section.get("received_at"),
            "stored_at": saved.isoformat(),
            "change_pct": float(change) if change is not None else None,
            "freshness": {
                "status": "unknown",
                "observation_age_days": (now.date() - day).days,
                "reason": "일별 저장 관측값 · 발표 시각 및 최신성 미확인",
            },
        }
    )
    if change is not None:
        out["change_reason"] = None
    return out


def _candidates(state: Path, market: str, now: datetime, scan: dict):
    """날짜를 직접 방문한다. 디렉터리/파일 수·크기 모두 제한하고 링크는 따라가지 않는다."""
    root = state / "runs"
    if root.is_symlink():
        scan["skipped"] += 1
        return
    for offset in range(LOOKBACK_DAYS):
        day = root / (now.astimezone(KST).date() - timedelta(days=offset)).isoformat()
        directory = day / market
        if day.is_symlink() or directory.is_symlink():
            scan["skipped"] += 1
            continue
        try:
            with os.scandir(directory) as entries:
                bounded = list(islice(entries, MAX_ENTRIES - scan["entries_scanned"] + 1))
        except OSError:
            continue
        remaining = MAX_ENTRIES - scan["entries_scanned"]
        scan["truncated"] |= len(bounded) > remaining
        bounded = bounded[:remaining]
        scan["entries_scanned"] += len(bounded)
        candidates = []
        for entry in bounded:
            if not entry.is_dir(follow_symlinks=False):
                continue
            path = Path(entry.path) / "00_inputs.json"
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                stat = path.stat()
                candidates.append((stat.st_mtime, str(path), stat.st_size))
            except OSError:
                scan["skipped"] += 1
        for modified, name, size in sorted(candidates, reverse=True):
            if scan["files_checked"] >= MAX_FILES:
                scan["truncated"] = True
                return
            scan["files_checked"] += 1
            if size > MAX_FILE_BYTES:
                scan["skipped"] += 1
                continue
            if scan["bytes_read"] + size > MAX_TOTAL_BYTES:
                scan["truncated"] = True
                return
            yield Path(name), datetime.fromtimestamp(modified, UTC)
        if scan["entries_scanned"] >= MAX_ENTRIES:
            scan["truncated"] = True
            return


def overview(state: Path, market: str, *, now: datetime) -> dict:
    """조회 범위 안에서 항목별 최신 유효 관측값. 등락률은 저장된 원천값만 사용한다."""
    if market not in ("KR", "US"):
        raise ValueError("시장은 KR 또는 US")
    now = now.astimezone(UTC)
    items = {key: _empty(key) for key in INSTRUMENTS}
    scan = {
        "lookback_days": LOOKBACK_DAYS,
        "max_files": MAX_FILES,
        "files_checked": 0,
        "files_read": 0,
        "bytes_read": 0,
        "entries_scanned": 0,
        "skipped": 0,
        "truncated": False,
    }
    for path, saved in _candidates(state, market, now, scan):
        if saved > now:
            scan["skipped"] += 1
            continue
        try:
            with path.open("rb") as file:
                raw = file.read(min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - scan["bytes_read"]) + 1)
            scan["bytes_read"] += len(raw)
            if len(raw) > MAX_FILE_BYTES or scan["bytes_read"] > MAX_TOTAL_BYTES:
                scan["skipped"] += 1
                scan["truncated"] = True
                continue
            payload = json.loads(raw)
            scan["files_read"] += 1
            if not isinstance(payload, dict):
                raise ValueError("invalid snapshot")
            meta = payload.get("meta") or {}
            if not isinstance(meta, dict) or meta.get("market", market) != market:
                raise ValueError("invalid market")
            for field in ("asof", "generated_at"):
                if meta.get(field) is not None:
                    stamp = _timestamp(meta[field])
                    if stamp is None or stamp > now:
                        raise ValueError("invalid snapshot timestamp")
            news_macro = payload.get("news_macro") or {}
            macro = news_macro.get("macro") if isinstance(news_macro, dict) else None
            if not isinstance(macro, dict):
                raise ValueError("invalid macro")
        except (OSError, ValueError, UnicodeError, RecursionError):
            scan["skipped"] += 1
            continue
        for key in items:
            candidate = _item(key, macro.get(key), saved, now)
            if candidate is None:
                continue
            current = items[key]
            candidate_order = (
                candidate["observed_date"],
                candidate["observed_at"] or "",
                candidate["stored_at"],
            )
            current_order = (
                current["observed_date"] or "",
                current["observed_at"] or "",
                current["stored_at"] or "",
            )
            if candidate_order > current_order:
                items[key] = candidate
    return {"market": market, "asof": now.isoformat(), "items": list(items.values()), "scan": scan}
