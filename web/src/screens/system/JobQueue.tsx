// 잡 큐 — GET /jobs/requests(상태 칩, 대기 중 행만 「취소」 → DELETE /jobs/{id}), GET /jobs kind 표(kind 마다 RunJobButton —
// trading 은 kind 값, realAccounts 는 GET /accounts 의 실전 계좌)와 일정(다음 실행).
import { useMemo, useState } from "react";
import { Chip } from "../../components/Chip";
import { RunJobButton } from "../../components/RunJobButton";
import { Table, type Column } from "../../components/Table";
import { deleteJson, errorText } from "../../lib/api";
import { fmtTime } from "../../lib/format";
import { useResource } from "../../lib/hooks";
import { body } from "../performance/ResourceView";
import { jobRows, requestRows, type JobsResp, type KindRow, type RequestRow, type RequestsResp, type ScheduleRow } from "./system.ts";

const REQ_PARAMS = { limit: "30" };
type AccountLite = { id: string; env: string };

const SCHEDULE_COLUMNS: Column<ScheduleRow>[] = [
  { key: "id", label: "일정", width: "40%", long: true },
  { key: "label", label: "종류", width: "25%" },
  { key: "trigger", label: "방식", width: "15%" },
  { key: "next_at", label: "다음 실행", width: "20%", align: "num", render: (r) => fmtTime(r.next_at) },
];

export function JobQueue() {
  const requests = useResource<RequestsResp>("/jobs/requests", REQ_PARAMS, ["jobs"]);
  const jobs = useResource<JobsResp>("/jobs", undefined, ["jobs"]);
  const accounts = useResource<AccountLite[]>("/accounts", undefined, ["accounts"]);
  const [error, setError] = useState<string | null>(null);
  const settled = accounts.data !== null || accounts.error !== null;
  const realAccounts = useMemo(() => (accounts.data ?? []).filter((a) => a.env === "real").map((a) => a.id), [accounts.data]);
  const reloadRequests = requests.reload;

  const reqColumns = useMemo<Column<RequestRow>[]>(() => {
    const cancel = async (id: number) => {
      setError(null);
      try {
        await deleteJson(`/jobs/${id}`);
        reloadRequests();
      } catch (e) {
        setError(errorText(e));
      }
    };
    return [
      { key: "id", label: "#", width: "6%", align: "num" },
      { key: "label", label: "잡", width: "22%", long: true },
      { key: "status", label: "상태", width: "10%", align: "chip", render: (r) => <Chip tone={r.tone}>{r.status}</Chip> },
      { key: "requested_at", label: "요청", width: "11%", align: "num", render: (r) => fmtTime(r.requested_at) },
      { key: "finished_at", label: "끝", width: "11%", align: "num", render: (r) => fmtTime(r.finished_at) },
      { key: "params", label: "인자", width: "18%" },
      { key: "error", label: "오류", width: "14%", render: (r) => r.error ?? "" },
      {
        key: "cancel",
        label: "",
        width: "8%",
        render: (r) =>
          r.cancellable ? (
            <button type="button" className="btn" onClick={() => void cancel(r.id)}>
              취소
            </button>
          ) : null,
      },
    ];
  }, [reloadRequests]);

  const kindColumns = useMemo<Column<KindRow>[]>(
    () => [
      { key: "label", label: "잡", width: "30%" },
      { key: "kind", label: "kind", width: "25%" },
      { key: "lane", label: "레인", width: "15%" },
      {
        key: "run",
        label: "실행",
        width: "30%",
        render: (k) => (settled ? <RunJobButton kind={k.kind} label={k.label} trading={k.trading} realAccounts={realAccounts} onDone={reloadRequests} /> : "…"),
      },
    ],
    [settled, realAccounts, reloadRequests],
  );

  return (
    <>
      {body(requests, "잡 요청", (r) => (
        <>
          <p className="data-muted">
            {r.alive ? "데몬 동작 중" : "데몬 꺼짐"} · 진행 중 {r.active}
          </p>
          <Table columns={reqColumns} rows={requestRows(r.requests)} rowKey={(x) => x.id} empty="최근 요청 없음" />
          {error ? (
            <p className="data-error" role="alert">
              {error}
            </p>
          ) : null}
        </>
      ))}
      <h3 className="perf-sub">수동 실행</h3>
      {body(jobs, "잡 목록", (j) => {
        const rows = jobRows(j);
        return (
          <>
            <Table columns={kindColumns} rows={rows.kinds} rowKey={(k) => k.kind} empty="실행할 수 있는 잡 없음" />
            <h3 className="perf-sub">일정</h3>
            <Table columns={SCHEDULE_COLUMNS} rows={rows.schedules} rowKey={(s) => s.id} empty="일정 없음" />
          </>
        );
      })}
    </>
  );
}
