// 표 — 넓은 폭(≥1024px)은 <table>(table-layout: fixed, 열마다 width), 좁은 폭은 RowCard 목록.
// 열은 숨기지 않는다. 넘치면 가로 스크롤 + 첫 열 고정. long 열은 표당 하나(말줄임 + 말풍선).
import { Fragment, useState, type ReactNode } from "react";
import { useWide } from "../lib/viewport";
import { Chip } from "./Chip";
import { Empty } from "./Empty";
import { RowCard, cellOf, fullTextOf, oneLong } from "./RowCard";
import { Tooltip } from "./Tooltip";

/** 표 열. 표당 long 은 하나 — 여럿이면 첫 번째만 long(말줄임·제목 줄)으로, 나머지는 text 로 그린다. */
export type Column<T> = {
  key: string;
  label: string;
  width: string;
  align?: "num" | "text" | "chip";
  long?: boolean;
  render?: (row: T) => ReactNode;
};

export type TableProps<T> = {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string | number;
  expand?: (row: T) => ReactNode;
  empty?: string;
  partial?: boolean;
  csvHref?: string;
};

function cellClass<T>(col: Column<T>, first: boolean): string {
  const cls = [`cell-${col.align ?? "text"}`];
  if (col.long) cls.push("cell-long");
  if (first) cls.push("cell-first");
  return cls.join(" ");
}

export function Table<T>({ columns: given, rows, rowKey, expand, empty, partial, csvHref }: TableProps<T>) {
  const columns = oneLong(given);
  const wide = useWide();
  const [openKeys, setOpenKeys] = useState<Set<string | number>>(() => new Set());
  const toggle = (k: string | number) =>
    setOpenKeys((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });

  const bar =
    partial || csvHref ? (
      <div className="table-bar">
        {partial ? <Chip tone="warn">일부 레인 응답 없음</Chip> : <span />}
        {csvHref ? (
          <a className="table-csv" href={csvHref} download>
            CSV
          </a>
        ) : null}
      </div>
    ) : null;

  if (!rows.length) {
    return (
      <div className="table-block">
        {bar}
        <Empty title={empty ?? "표시할 항목이 없습니다"} />
      </div>
    );
  }

  if (!wide) {
    return (
      <div className="table-block">
        {bar}
        <ul className="row-cards">
          {rows.map((row) => {
            const k = rowKey(row);
            return (
              <RowCard
                key={k}
                columns={columns}
                row={row}
                expanded={expand ? expand(row) : undefined}
                open={openKeys.has(k)}
                onToggle={expand ? () => toggle(k) : undefined}
              />
            );
          })}
        </ul>
      </div>
    );
  }

  return (
    <div className="table-block">
      {bar}
      <div className="table-scroll">
        <table className="table">
          <colgroup>
            {columns.map((c) => (
              <col key={c.key} style={{ width: c.width }} />
            ))}
          </colgroup>
          <thead>
            <tr>
              {columns.map((c, i) => (
                <th key={c.key} scope="col" className={cellClass(c, i === 0)}>
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const k = rowKey(row);
              const open = openKeys.has(k);
              return (
                <Fragment key={k}>
                  <tr>
                    {columns.map((c, i) => {
                      const content = cellOf(c, row);
                      const full = c.long ? fullTextOf(c, row) : undefined;
                      return (
                        <td key={c.key} className={cellClass(c, i === 0)} title={full}>
                          {i === 0 && expand ? (
                            <button type="button" className="row-toggle" aria-expanded={open} aria-label={open ? "접기" : "펼치기"} onClick={() => toggle(k)}>
                              {open ? "▾" : "▸"}
                            </button>
                          ) : null}
                          {full !== undefined ? (
                            <Tooltip text={full}>
                              <span className="cell-ellipsis">{content}</span>
                            </Tooltip>
                          ) : (
                            content
                          )}
                        </td>
                      );
                    })}
                  </tr>
                  {open && expand ? (
                    <tr className="table-expand">
                      <td colSpan={columns.length}>{expand(row)}</td>
                    </tr>
                  ) : null}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
