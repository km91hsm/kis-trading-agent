// 이벤트(공시) — GET /news/events?market=&hours=72&limit=100. 중요도 칩(critical 빨강·high 노랑·그 밖 회색).
import { useMemo } from "react";
import { Chip } from "../../components/Chip";
import { Table, type Column } from "../../components/Table";
import type { Tone } from "../../components/Badge";
import { fmtDate, fmtTime } from "../../lib/format";
import { useResource } from "../../lib/hooks";
import { body } from "../performance/ResourceView";
import type { Market } from "./Data.tsx";

type Disclosure = { id: number; source: string; title: string; url: string | null; published_at: string | null; received_at: string; severity: string | null; tickers: string[] };
type EventsResp = { market: string; hours: number; asof: string | null; disclosures: Disclosure[] };

const TONE: Record<string, Tone> = { critical: "fail", high: "warn" };

export function Events({ market }: { market: Market }) {
  const params = useMemo(() => ({ market, hours: "72", limit: "100" }), [market]);
  const res = useResource<EventsResp>("/news/events", params, ["news"]);
  const columns: Column<Disclosure>[] = [
    { key: "at", label: "시각", width: "16%", render: (d) => when(d.published_at ?? d.received_at) },
    { key: "severity", label: "중요도", width: "12%", align: "chip", render: (d) => <Chip tone={TONE[d.severity ?? ""] ?? "muted"}>{d.severity ?? "—"}</Chip> },
    { key: "title", label: "제목", width: "46%", long: true, render: (d) => (d.url ? <a href={d.url} target="_blank" rel="noreferrer">{d.title}</a> : d.title) },
    { key: "tickers", label: "종목", width: "16%", render: (d) => d.tickers.join(", ") || "—" },
    { key: "source", label: "출처", width: "10%", render: (d) => d.source },
  ];
  return body(res, "이벤트", (d) => <Table columns={columns} rows={d.disclosures} rowKey={(x) => x.id} empty="공시 없음" />);
}

export function when(iso: string | null): string {
  return iso ? `${fmtDate(iso)} ${fmtTime(iso)}` : "—";
}
