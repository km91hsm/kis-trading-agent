// 행 카드 — 좁은 폭에서 표의 한 행. 1줄 메타(align:"chip" 열), 2줄 제목(long 열, 말줄임), 3줄 나머지 열을 2열 정의 목록(라벨·값,
// 값은 줄바꿈 — 가로 스크롤 칩 줄로 잘리지 않게), 4줄 행동 열(action: 취소 버튼 등)은 자기 줄.
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

/** 표당 long 은 하나 — 첫 long 열만 남기고 나머지 long 표시는 떼어 낸다. */
export function oneLong<T>(columns: Column<T>[]): Column<T>[] {
  const first = columns.findIndex((c) => c.long);
  return columns.map((c, i) => (c.long && i !== first ? { ...c, long: false } : c));
}

export function RowCard<T>({
  columns: given,
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
  const columns = oneLong(given);
  const meta = columns.filter((c) => c.align === "chip");
  const long = columns.find((c) => c.long && c.align !== "chip");
  const actions = columns.filter((c) => c.action);
  const rest = columns.filter((c) => c !== long && c.align !== "chip" && !c.action);
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
        <dl className="row-card-fields">
          {rest.map((c) => (
            <div key={c.key} className="row-card-field">
              <dt>{c.label}</dt>
              <dd className={c.align === "num" ? "num" : undefined}>{cellOf(c, row)}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {actions.length ? (
        <div className="row-card-actions">
          {actions.map((c) => (
            <span key={c.key}>{cellOf(c, row)}</span>
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
