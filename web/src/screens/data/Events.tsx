// 공시 — GET /news/events?market=&hours=72&limit=100. 30행 `Pager`(?epage — 같은 페이지의 뉴스 표와 다른 param).
// 날짜만(시각은 의미 없음), 중요도 열·종목 열은 값이 하나라도 있을 때만(중요도 칩 긴급 빨강·중요 노랑), 종목은 코드+이름(/names), 출처 한글.
import { useMemo } from "react";
import { Chip } from "../../components/Chip";
import { Pager } from "../../components/Pager";
import { Table, type Column } from "../../components/Table";
import { fmtDate } from "../../lib/format";
import { useResource } from "../../lib/hooks";
import { tickerLabel } from "../../lib/ticker";
import { usePaged } from "../../lib/usePaged";
import { body } from "../performance/ResourceView";
import type { Market } from "./Data.tsx";
import { severityChip, sourceLabel, type Names } from "./data.ts";

type Disclosure = { id: number; source: string; title: string; url: string | null; published_at: string | null; received_at: string; severity: string | null; tickers: string[] };
type EventsResp = { market: string; hours: number; asof: string | null; disclosures: Disclosure[] };

const EMPTY: Disclosure[] = [];

export function Events({ market }: { market: Market }) {
  const params = useMemo(() => ({ market, hours: "72", limit: "100" }), [market]);
  const res = useResource<EventsResp>("/news/events", params, ["news"]);
  const all = res.data?.disclosures ?? EMPTY;
  const paged = usePaged(all, 30, "epage");
  const hasTickers = all.some((d) => d.tickers.length);
  const hasSeverity = all.some((d) => !!d.severity);
  const tickers = useMemo(() => Array.from(new Set(paged.rows.flatMap((d) => d.tickers))).join(","), [paged.rows]);
  const nameParams = useMemo(() => ({ tickers, market }), [tickers, market]);
  const names = useResource<Names>("/names", nameParams, ["accounts"], { enabled: !!tickers });
  const label = (t: string) => tickerLabel(t, names.data?.[`${market}:${t}`] ?? null);
  const columns: Column<Disclosure>[] = [
    { key: "at", label: "날짜", width: "12%", render: (d) => fmtDate(d.published_at ?? d.received_at) },
    ...(hasSeverity
      ? [
          {
            key: "severity",
            label: "중요도",
            width: "10%",
            align: "chip" as const,
            render: (d: Disclosure) => {
              const c = severityChip(d.severity);
              return c ? <Chip tone={c.tone}>{c.label}</Chip> : "—";
            },
          },
        ]
      : []),
    { key: "title", label: "제목", width: "auto", long: true, render: (d) => (d.url ? <a href={d.url} target="_blank" rel="noreferrer">{d.title}</a> : d.title) },
    ...(hasTickers ? [{ key: "tickers", label: "종목", width: "20%", render: (d: Disclosure) => d.tickers.map(label).join(", ") || "—" }] : []),
    { key: "source", label: "출처", width: "14%", render: (d) => sourceLabel(d.source) },
  ];
  return body(res, "공시", () => (
    <>
      <Table columns={columns} rows={paged.rows} rowKey={(x) => x.id} empty="공시 없음" />
      <Pager {...paged} />
    </>
  ));
}
