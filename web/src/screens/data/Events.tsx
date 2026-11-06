// 공시 — GET /news/events?market=&hours=72&limit=100. 30행 `Pager`(?epage — 같은 페이지의 뉴스 표와 다른 param).
// 날짜만(시각은 의미 없음), 중요도 칩은 값 있을 때만(긴급 빨강·중요 노랑), 종목 열은 값이 하나라도 있을 때만, 출처 한글.
import { useMemo } from "react";
import { Chip } from "../../components/Chip";
import { Pager } from "../../components/Pager";
import { Table, type Column } from "../../components/Table";
import { fmtDate } from "../../lib/format";
import { useResource } from "../../lib/hooks";
import { usePaged } from "../../lib/usePaged";
import { body } from "../performance/ResourceView";
import type { Market } from "./Data.tsx";
import { severityChip, sourceLabel } from "./data.ts";

type Disclosure = { id: number; source: string; title: string; url: string | null; published_at: string | null; received_at: string; severity: string | null; tickers: string[] };
type EventsResp = { market: string; hours: number; asof: string | null; disclosures: Disclosure[] };

const EMPTY: Disclosure[] = [];

export function Events({ market }: { market: Market }) {
  const params = useMemo(() => ({ market, hours: "72", limit: "100" }), [market]);
  const res = useResource<EventsResp>("/news/events", params, ["news"]);
  const all = res.data?.disclosures ?? EMPTY;
  const paged = usePaged(all, 30, "epage");
  const hasTickers = all.some((d) => d.tickers.length);
  const columns: Column<Disclosure>[] = [
    { key: "at", label: "날짜", width: "12%", render: (d) => fmtDate(d.published_at ?? d.received_at) },
    {
      key: "severity",
      label: "중요도",
      width: "10%",
      align: "chip",
      render: (d) => {
        const c = severityChip(d.severity);
        return c ? <Chip tone={c.tone}>{c.label}</Chip> : "—";
      },
    },
    { key: "title", label: "제목", width: "auto", long: true, render: (d) => (d.url ? <a href={d.url} target="_blank" rel="noreferrer">{d.title}</a> : d.title) },
    ...(hasTickers ? [{ key: "tickers", label: "종목", width: "16%", render: (d: Disclosure) => d.tickers.join(", ") || "—" }] : []),
    { key: "source", label: "출처", width: "14%", render: (d) => sourceLabel(d.source) },
  ];
  return body(res, "공시", () => (
    <>
      <Table columns={columns} rows={paged.rows} rowKey={(x) => x.id} empty="공시 없음" />
      <Pager {...paged} />
    </>
  ));
}
