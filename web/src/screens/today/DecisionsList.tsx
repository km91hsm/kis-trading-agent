// 그날 결정 목록 — 레인 합본(`GET /decisions`), 시각 역순, 상세 링크(단타는 없음), CSV.
// 종목 칸은 코드+이름만, 내용 칸은 최종 의견 요약(summary) — 판단(HOLD/BUY)은 방향 열에만.
import { Link } from "react-router-dom";
import { Chip } from "../../components/Chip";
import { Table, type Column } from "../../components/Table";
import { csvUrl } from "../../lib/api";
import { fmtTime } from "../../lib/format";
import { decisionRows, LANE_LABEL, type Decision, type DecisionRow, type Lane } from "./today.ts";

const COLUMNS: Column<DecisionRow>[] = [
  { key: "at", label: "시각", width: "88px", render: (r) => fmtTime(r.at) },
  { key: "lane", label: "레인", width: "72px", align: "chip", render: (r) => <Chip>{LANE_LABEL[r.lane as Lane] ?? r.lane}</Chip> },
  { key: "ticker", label: "종목", width: "180px", long: true, render: (r) => (r.href ? <Link to={r.href}>{r.ticker}</Link> : r.ticker) },
  {
    key: "summary",
    label: "내용",
    width: "auto",
    long: true,
    render: (r) => (r.summary ? <span title={r.summary}>{r.summary}</span> : "—"),
  },
  { key: "side", label: "방향", width: "64px" },
  { key: "qty", label: "수량", width: "72px", align: "num", render: (r) => (r.qty ?? "—").toString() },
  { key: "account_id", label: "계좌", width: "112px", render: (r) => r.account_id ?? "—" },
  { key: "status", label: "상태", width: "72px", align: "chip", render: (r) => <span title={r.raw ?? undefined}>{r.status}</span> },
];

export function DecisionsList({ items, partial, date }: { items: Decision[]; partial: boolean; date: string }) {
  return (
    <Table
      columns={COLUMNS}
      rows={decisionRows(items)}
      rowKey={(r) => r.key}
      partial={partial}
      csvHref={items.length ? csvUrl("/decisions", { date }) : undefined}
      empty="이 날 결정이 없습니다"
    />
  );
}
