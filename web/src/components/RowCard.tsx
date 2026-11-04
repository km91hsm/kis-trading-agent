// 행 카드 — 좁은 폭에서 표의 한 행. 1줄 메타(align:"chip" 열), 2줄 제목(long 열, 말줄임), 3줄 나머지 열을 칩으로.
import type { ReactNode } from "react";
import type { Column } from "./Table";

/** 열의 칸 내용 — render 가 있으면 그것, 없으면 row[key] 문자열. */
export function cellOf<T>(col: Column<T>, row: T): ReactNode {
  if (col.render) return col.render(row);
  const v = (row as Record<string, unknown>)[col.key];
  return v === null || v === undefined ? "" : String(v);
}

/** 말줄임 칸의 전체 글(말풍선·title 용). 문자열·숫자 값일 때만. */
export function fullTextOf<T>(col: Column<T>, row: T): string | undefined {
  const v = (row as Record<string, unknown>)[col.key];
  return typeof v === "string" || typeof v === "number" ? String(v) : undefined;
}

export function RowCard<T>({
  columns,
  row,
  expanded,
  open = false,
  onToggle,
}: {
  columns: Column<T>[];
  row: T;
  expanded?: ReactNode;
  open?: boolean;
  onToggle?: () => void;
}) {
  const meta = columns.filter((c) => c.align === "chip");
  const long = columns.find((c) => c.long && c.align !== "chip");
  const rest = columns.filter((c) => c !== long && c.align !== "chip");
  return (
    <li className="row-card">
      {meta.length ? (
        <div className="row-card-meta">
          {meta.map((c) => (
            <span key={c.key}>{cellOf(c, row)}</span>
          ))}
        </div>
      ) : null}
      {long ? (
        <div className="row-card-title" title={fullTextOf(long, row)}>
          {cellOf(long, row)}
        </div>
      ) : null}
      {rest.length ? (
        <div className="row-card-chips">
          {rest.map((c) => (
            <span key={c.key} className={c.align === "num" ? "chip num" : "chip"} data-tone="muted">
              <span className="row-card-label">{c.label}</span> {cellOf(c, row)}
            </span>
          ))}
        </div>
      ) : null}
      {onToggle ? (
        <button type="button" className="row-toggle" aria-expanded={open} onClick={onToggle}>
          {open ? "접기" : "펼치기"}
        </button>
      ) : null}
      {open && expanded ? <div className="row-card-expand">{expanded}</div> : null}
    </li>
  );
}
