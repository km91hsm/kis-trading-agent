// 잡 큐 — GET /jobs/requests(상태 칩·인자 요약, 대기 중 행만 「취소」 → Confirm → DELETE /jobs/{id}),
// 수동 실행은 GET /jobs kind 를 레인별 버튼 묶음으로(RunJobButton — trading 은 kind 값, realAccounts 는 GET /accounts 의 실전 계좌).
// 일정 표는 데몬 절(같은 42행)에만 둔다.
import { useMemo, useState } from "react";
import { Chip } from "../../components/Chip";
import { Confirm } from "../../components/Confirm";
import { RunJobButton } from "../../components/RunJobButton";
import { Table, type Column } from "../../components/Table";
import { deleteJson, errorText } from "../../lib/api";
import { fmtWhen } from "../../lib/format";
import { termLabel } from "../../lib/glossary";
import { useResource } from "../../lib/hooks";
import { body } from "../performance/ResourceView";
import { jobRows, requestRows, type JobsResp, type KindRow, type RequestRow, type RequestsResp } from "./system.ts";

const REQ_PARAMS = { limit: "30" };
type AccountLite = { id: string; env: string };

/** 레인별 묶음(등장 순서 유지). */
function byLane(kinds: KindRow[]): [string, KindRow[]][] {
  const out = new Map<string, KindRow[]>();
  for (const k of kinds) out.set(k.lane, [...(out.get(k.lane) ?? []), k]);
  return Array.from(out.entries());
}

export function JobQueue() {
  const requests = useResource<RequestsResp>("/jobs/requests", REQ_PARAMS, ["jobs"]);
  const jobs = useResource<JobsResp>("/jobs", undefined, ["jobs"]);
  const accounts = useResource<AccountLite[]>("/accounts", undefined, ["accounts"]);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState<RequestRow | null>(null);
  const settled = accounts.data !== null || accounts.error !== null;
  const realAccounts = useMemo(() => (accounts.data ?? []).filter((a) => a.env === "real").map((a) => a.id), [accounts.data]);
  const reloadRequests = requests.reload;

  const cancel = async (id: number) => {
    setCancelling(null);
    setError(null);
    try {
      await deleteJson(`/jobs/${id}`);
      reloadRequests();
    } catch (e) {
      setError(errorText(e));
    }
  };

  const reqColumns = useMemo<Column<RequestRow>[]>(
    () => [
      { key: "id", label: "#", width: "6%", align: "num" },
      { key: "label", label: "잡", width: "22%", long: true },
      { key: "status", label: "상태", width: "10%", align: "chip", render: (r) => <Chip tone={r.tone}>{r.status}</Chip> },
      { key: "requested_at", label: "요청", width: "12%", align: "num", render: (r) => fmtWhen(r.requested_at) },
      { key: "finished_at", label: "끝", width: "12%", align: "num", render: (r) => fmtWhen(r.finished_at) },
      { key: "params", label: "인자", width: "14%" },
      { key: "error", label: "오류", width: "16%", render: (r) => (r.error ? <span className="cell-ellipsis" title={r.error}>{r.error}</span> : "") },
      {
        key: "cancel",
        label: "",
        width: "8%",
        render: (r) =>
          r.cancellable ? (
            <button type="button" className="btn" onClick={() => setCancelling(r)}>
              취소
            </button>
          ) : null,
      },
    ],
    [],
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
          <Confirm
            open={cancelling !== null}
            title={`${cancelling?.label ?? ""} 취소`}
            body={<p>대기열에서 뺍니다. 이미 시작한 잡은 멈추지 않습니다.</p>}
            onConfirm={() => cancelling && void cancel(cancelling.id)}
            onClose={() => setCancelling(null)}
          />
        </>
      ))}
      <h3 className="perf-sub">수동 실행</h3>
      {body(jobs, "잡 목록", (j) => {
        const rows = jobRows(j);
        if (!rows.kinds.length) return <p className="data-muted">실행할 수 있는 잡 없음</p>;
        return (
          <div className="job-grid">
            {byLane(rows.kinds).map(([lane, kinds]) => (
              <div key={lane} className="job-lane" data-lane={lane}>
                <h4>{termLabel(`lane.${lane}`)}</h4>
                <div className="job-lane-buttons">
                  {kinds.map((k) =>
                    settled ? (
                      <span key={k.kind} title={k.kind}>
                        <RunJobButton kind={k.kind} label={k.label} trading={k.trading} realAccounts={realAccounts} onDone={reloadRequests} />
                      </span>
                    ) : (
                      <span key={k.kind}>…</span>
                    ),
                  )}
                </div>
              </div>
            ))}
          </div>
        );
      })}
      <p className="data-muted">
        정기 일정과 다음 실행 시각은 <a href="#daemon">데몬 ›</a> 절에.
      </p>
    </>
  );
}
