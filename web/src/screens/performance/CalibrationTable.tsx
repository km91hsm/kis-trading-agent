// 보정 표 — run(최신 먼저)×역할 적중률·이웃 적중률·편향. 표본 부족 run 은 한 줄.
import { Chip } from "../../components/Chip";
import { Table, type Column } from "../../components/Table";
import { calibrationBins, type Calibration, type CalibrationRow } from "./performance.ts";

export function CalibrationTable({ data }: { data: Calibration }) {
  const columns: Column<CalibrationRow>[] = [
    { key: "created_at", label: "실행", width: "14%", render: (r) => r.created_at },
    { key: "active", label: "적용", width: "10%", align: "chip", render: (r) => <Chip tone={r.active ? "ok" : "muted"}>{r.active ? "적용" : "미적용"}</Chip> },
    { key: "role", label: "역할", width: "14%", long: true, render: (r) => r.role },
    { key: "hit_rate", label: "적중률", width: "12%", align: "num", render: (r) => r.hit_rate },
    { key: "n", label: "n", width: "8%", align: "num", render: (r) => (r.n === null ? "—" : String(r.n)) },
    { key: "neighbor", label: "이웃 적중률", width: "14%", align: "num", render: (r) => r.neighbor_hit_rate },
    { key: "bias", label: "편향", width: "10%", align: "num", render: (r) => r.bias },
    { key: "note", label: "비고", width: "18%", render: (r) => r.note || "—" },
  ];
  return <Table columns={columns} rows={calibrationBins(data)} rowKey={(r) => r.key} empty="보정 기록 없음" />;
}
