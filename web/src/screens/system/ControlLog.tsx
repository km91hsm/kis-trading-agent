// 조작 이력 — GET /system/control-log?limit=500(최신순)을 30행씩 ?page=N 으로: 시각 · 채널 · 누가 · 키 · 변경(전 → 후).
import { Chip } from "../../components/Chip";
import { Pager } from "../../components/Pager";
import { Table, type Column } from "../../components/Table";
import { fmtWhen } from "../../lib/format";
import { useResource } from "../../lib/hooks";
import { usePaged } from "../../lib/usePaged";
import { body } from "../performance/ResourceView";
import { controlRows, type ControlEntry, type ControlRow } from "./system.ts";

const PARAMS = { limit: "500" };

const COLUMNS: Column<ControlRow>[] = [
  { key: "at", label: "시각", width: "14%", align: "num", render: (r) => fmtWhen(r.at) },
  { key: "channel", label: "채널", width: "12%", align: "chip", render: (r) => <Chip>{r.channel}</Chip> },
  { key: "actor", label: "누가", width: "12%" },
  { key: "key", label: "키", width: "28%", long: true, render: (r) => <code>{r.key}</code> },
  { key: "change", label: "변경", width: "34%", render: (r) => `${r.old} → ${r.new}` },
];

function PagedLog({ rows }: { rows: ControlRow[] }) {
  const paged = usePaged(rows);
  return (
    <>
      <Table columns={COLUMNS} rows={paged.rows} rowKey={(r) => r.id} empty="조작 이력 없음" />
      <Pager {...paged} />
    </>
  );
}

export function ControlLog() {
  const res = useResource<ControlEntry[]>("/system/control-log", PARAMS, ["control"]);
  return body(res, "조작 이력", (log) => <PagedLog rows={controlRows(log)} />);
}
