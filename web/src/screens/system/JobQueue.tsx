// 잡 큐 — 요청 기록을 먼저, 수동 실행(GET /jobs kind 레인별 버튼)은 기본 접기. 진행 중·실패부터 확인한다.
// 상태 칩·인자 요약, 대기 중 행만 「취소」 → Confirm → DELETE /jobs/{id})(RunJobButton — trading 은 kind 값, realAccounts 는 GET /accounts 의 실전 계좌).
// 일정 표는 상태 페이지의 데몬 절(같은 42행)에만 둔다 — 「데몬 ›」 링크.
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Chip } from "../../components/Chip";
import { Confirm } from "../../components/Confirm";
import { Pager } from "../../components/Pager";
import { RunJobButton } from "../../components/RunJobButton";
import { Table, type Column } from "../../components/Table";
import { deleteJson, errorText } from "../../lib/api";
import { fmtWhen } from "../../lib/format";
import { termLabel } from "../../lib/glossary";
import { useResource } from "../../lib/hooks";
import { pagePathOf } from "../../lib/nav";
import { PAGE_SIZE, usePaged, usePageReset } from "../../lib/usePaged";
import { body } from "../performance/ResourceView";
import { jobRows, requestRows, type JobRequest, type JobsResp, type KindRow, type RequestRow, type RequestsResp } from "./system.ts";
import { JobActivity } from "./JobActivity";
import "./JobQueue.css";

const REQ_PARAMS = { limit: "200" };
type AccountLite = { id: string; env: string };

const LANE_ORDER = ["fast", "reeval", "slow", "info"];
type RequestSort = "active" | "recent";

/** 실행 버튼은 고정 레인 순서, 같은 레인 안에서는 이름순. 실제 큐 우선순위와 무관하다. */
function byLane(kinds: KindRow[]): [string, KindRow[]][] {
  const out = new Map<string, KindRow[]>();
  const sorted = [...kinds].sort((a, b) => a.label.localeCompare(b.label, "ko") || a.kind.localeCompare(b.kind));
  for (const k of sorted) out.set(k.lane, [...(out.get(k.lane) ?? []), k]);
  const rank = (lane: string) => {
    const index = LANE_ORDER.indexOf(lane);
    return index < 0 ? LANE_ORDER.length : index;
  };
  return Array.from(out.entries()).sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b));
}

function timestamp(value: string | null): number {
  const time = value ? Date.parse(value) : NaN;
  return Number.isFinite(time) ? time : 0;
}

/** 표시 순서만 바꾼다. 대기는 큐 저장소와 같은 priority DESC, id ASC. */
function sortedRequests(requests: JobRequest[], sort: RequestSort, status: string): JobRequest[] {
  const rank = (request: JobRequest) => request.status === "running" ? 0 : request.status === "queued" ? 1 : request.status === "error" && request.outcome !== "interrupted" && request.outcome !== "followed_by_success" ? 2 : 3;
  return requests.filter((request) => !status || (["failed", "interrupted", "followed_by_success"].includes(status) ? request.outcome === status : request.status === status)).sort((a, b) => {
    if (sort === "recent") return timestamp(b.requested_at) - timestamp(a.requested_at) || b.id - a.id;
    const group = rank(a) - rank(b);
    if (group) return group;
    if (rank(a) < 2) return (b.priority ?? 0) - (a.priority ?? 0) || a.id - b.id;
    return (timestamp(b.finished_at) || timestamp(b.requested_at)) - (timestamp(a.finished_at) || timestamp(a.requested_at)) || b.id - a.id;
  });
}

/** 요청 기록 30행씩 ?rpage=N(같은 페이지의 실행 기록 ?page 와 따로) — body() 콜백 안에서는 훅을 못 쓰므로 부품으로. */
function PagedRequests({ requests, columns }: { requests: JobRequest[]; columns: Column<RequestRow>[] }) {
  const [sort, setSort] = useState<RequestSort>("active");
  const [status, setStatus] = useState("");
  const rows = useMemo(() => requestRows(sortedRequests(requests, sort, status)), [requests, sort, status]);
  const paged = usePaged(rows, PAGE_SIZE, "rpage");
  const resetPage = usePageReset("rpage");
  return (
    <div className="job-requests" data-pagination-region tabIndex={-1} role="region" aria-label="요청 기록">
      <div className="perf-filters job-request-filters">
        <label>
          정렬
          <select aria-label="요청 정렬" value={sort} onChange={(event) => { setSort(event.target.value as RequestSort); resetPage(); }}>
            <option value="active">진행 우선</option>
            <option value="recent">최신 요청순</option>
          </select>
        </label>
        <label>
          상태
          <select aria-label="요청 상태" value={status} onChange={(event) => { setStatus(event.target.value); resetPage(); }}>
            <option value="">전체</option>
            <option value="running">실행 중</option>
            <option value="queued">대기</option>
            <option value="ok">완료</option>
            <option value="failed">확인 필요</option>
            <option value="interrupted">종료 요청 중단</option>
            <option value="followed_by_success">후속 정상 실행</option>
            <option value="error">원장 오류 전체</option>
            <option value="skipped">건너뜀</option>
          </select>
        </label>
        <span className="data-muted" aria-live="polite">{rows.length} / {requests.length}건</span>
      </div>
      <p className="data-muted job-request-guide">
        {sort === "active" ? "실행 중 → 대기(우선순위·접수 순) → 확인 필요 → 나머지 종료(종료 시각 최신순)" : "요청 시각이 최신인 순서"}
        <br />화면 정렬은 실제 실행 순서를 변경하지 않습니다. 실제 실행은 작업 종류와 데몬 상태에 따라 달라집니다.
      </p>
      <Table columns={columns} rows={paged.rows} rowKey={(x) => x.id} empty={status ? "선택한 상태의 요청 없음" : "최근 요청 없음"} />
      <Pager {...paged} />
    </div>
  );
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
      { key: "error", label: "오류", width: "16%", render: (r) => (
        <>
          {r.error ? <span className="cell-ellipsis" title={r.error}>{r.error}</span> : null}
          {r.followup_at ? <small className="data-muted job-followup">후속 정상 시각 {fmtWhen(r.followup_at)}</small> : null}
          {r.scope_confirmed === false && r.tone === "fail" ? <small className="data-muted job-followup">대상 범위 미확인</small> : null}
        </>
      ) },
      {
        key: "cancel",
        label: "",
        width: "8%",
        action: true,
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
    <div className="job-queue">
      <JobActivity />
      <h3 className="perf-sub" id="job-requests" tabIndex={-1}>요청 큐 기록</h3>
      {body(requests, "잡 요청", (r) => (
        <>
          <p className="data-muted job-request-summary">
            {r.alive ? "데몬 동작 중" : "데몬 꺼짐"} · 요청 대기·실행 중 {r.active}건
            <br />수동 요청·자동 등록 요청을 포함한 최근 200건 기준이며, 실행 중·대기 요청은 모두 포함합니다. 예약 작업의 직접 실행은 위의 실행 단계에서 확인합니다.
          </p>
          <PagedRequests requests={r.requests} columns={reqColumns} />
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
      <details className="job-manual">
        <summary>수동 실행</summary>
        <p className="data-muted">작업을 선택하면 대상과 주문·AI 영향을 확인한 뒤 요청을 등록합니다.</p>
        {body(jobs, "잡 목록", (j) => {
          const rows = jobRows(j);
          if (!rows.kinds.length) return <p className="data-muted">실행할 수 있는 잡 없음</p>;
          return (
            <div className="job-grid">
              {byLane(rows.kinds).map(([lane, kinds]) => (
                <div key={lane} className="job-lane" data-lane={lane}>
                  <h4>{termLabel(`lane.${lane}`)} <span className="num">{kinds.length}</span></h4>
                  <div className="job-lane-buttons">
                    {kinds.map((k) =>
                      settled ? (
                        <div key={k.kind} title={k.kind}>
                          <RunJobButton kind={k.kind} label={k.label} trading={k.trading} realAccounts={realAccounts} onDone={reloadRequests} />
                        </div>
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
      </details>
      <p className="data-muted">
        정기 일정과 다음 실행 시각은 <Link to={`${pagePathOf("system", "daemon")}#daemon`}>데몬 ›</Link> 절에.
      </p>
    </div>
  );
}
