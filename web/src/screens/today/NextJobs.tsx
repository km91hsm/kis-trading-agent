// 다음 할 일 — 데몬 사용자 잡(스캔·리밸런스·리포트·복기…) 중 다음 실행이 가까운 5개를 표로. 내부 틱·분 단위 감시는 시스템 › 상태 › 데몬 표에만.
import { Chip } from "../../components/Chip";
import { Empty } from "../../components/Empty";
import { Table, type Column } from "../../components/Table";
import type { ApiError } from "../../lib/api";
import { useNow } from "../../lib/hooks";
import { nextJobs, type Daemon, type NextJob } from "./today.ts";

const LAST: Record<string, { label: string; tone: "ok" | "fail" | "muted" | "warn" }> = {
  ok: { label: "완료", tone: "ok" },
  skipped: { label: "건너뜀", tone: "muted" },
  error: { label: "실패", tone: "fail" },
  failed: { label: "실패", tone: "fail" },
  running: { label: "실행 중", tone: "warn" },
};

const COLUMNS: Column<NextJob>[] = [
  {
    key: "label",
    label: "작업",
    width: "40%",
    long: true,
    render: (j) => (
      <>
        {j.label}
        {j.market ? <> <Chip>{j.market}</Chip></> : null}
      </>
    ),
  },
  { key: "when", label: "예정", width: "30%", align: "num", render: (j) => `${j.when} · ${j.rel}` },
  {
    key: "last",
    label: "마지막",
    width: "30%",
    align: "chip",
    render: (j) => (j.last ? <Chip tone={LAST[j.last]?.tone ?? "muted"}>{LAST[j.last]?.label ?? j.last}</Chip> : "—"),
  },
];

export function NextJobs({ data, error }: { data: Daemon | null; error: ApiError | null }) {
  const now = useNow(60_000);
  if (error) return <Empty title="데몬 상태를 불러오지 못했습니다">{error.message}</Empty>;
  if (!data) return <Empty title="불러오는 중" />;
  if (!data.alive) return <Empty title="데몬 꺼짐" />;
  return <Table columns={COLUMNS} rows={nextJobs(data, now)} rowKey={(j) => j.id} empty="예정된 작업이 없습니다" />;
}
