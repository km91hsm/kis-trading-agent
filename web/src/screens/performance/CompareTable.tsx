// 계좌×시장 비교 표 — metrics null 행은 「NAV 없음」.
import { Chip } from "../../components/Chip";
import { Table, type Column } from "../../components/Table";
import { compareRows, type Compare, type CompareRow } from "./performance.ts";

const LANE_LABEL: Record<string, string> = { spot: "현물", scalp: "단타", fo: "파생" };

export function CompareTable({ data }: { data: Compare }) {
  const columns: Column<CompareRow>[] = [
    { key: "account", label: "계좌", width: "18%", long: true, render: (r) => `${r.account_id} · ${r.market}` },
    { key: "env", label: "구분", width: "10%", align: "chip", render: (r) => <Chip tone={r.env === "paper" ? "muted" : "live"}>{r.env === "paper" ? "모의" : "실전"}</Chip> },
    { key: "lane", label: "레인", width: "10%", align: "chip", render: (r) => <Chip>{LANE_LABEL[r.lane] ?? r.lane}</Chip> },
    { key: "ret", label: "수익률", width: "14%", align: "num", render: (r) => r.ret },
    { key: "mdd", label: "MDD", width: "14%", align: "num", render: (r) => r.mdd },
    { key: "sortino", label: "Sortino", width: "12%", align: "num", render: (r) => r.sortino },
    { key: "note", label: "비고", width: "22%", render: (r) => r.note || "—" },
  ];
  return <Table columns={columns} rows={compareRows(data)} rowKey={(r) => r.key} empty="비교할 계좌 없음" />;
}
