"""표 → CSV 응답 (`?format=csv`). 옛 `web/routers/_common.csv_response` 이식."""

from __future__ import annotations

import csv
import io
import json

from fastapi.responses import Response


def _cell(v: object) -> object:
    if v is None:
        return ""
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False, default=str)
    return v


def csv_response(rows: list[dict], filename: str, columns: list[str] | None = None) -> Response:
    """UTF-8 BOM(엑셀 한글 호환) CSV. 열 순서는 columns(없으면 첫 행 키 순), None 은 빈 칸,
    목록·사전은 JSON 문자열."""
    cols = columns or (list(rows[0].keys()) if rows else [])
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: _cell(r.get(k)) for k in cols})
    return Response(
        "﻿" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
