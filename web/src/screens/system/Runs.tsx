// runs·calls — 날짜(DatePicker)·kind 로 GET /runs(최대 200, 30행씩 ?page=N, + CSV 링크), run 번호(링크 모양 버튼)를 누르면 GET /calls?run_id=(비용·소요·턴, + CSV).
import { useMemo, useState } from "react";
import { Chip } from "../../components/Chip";
import { DatePicker } from "../../components/DatePicker";
import { Pager } from "../../components/Pager";
import { Table, type Column } from "../../components/Table";
import { csvUrl } from "../../lib/api";
import { fmtWhen } from "../../lib/format";
import { useKstDate, useResource } from "../../lib/hooks";
import { usePaged, usePageReset } from "../../lib/usePaged";
import { body } from "../performance/ResourceView";
import { callRows, kindLabel, runRows, statusOf, type CallRow, type CallsResp, type JobsResp, type Run, type RunRow } from "./system.ts";

const CALL_COLUMNS: Column<CallRow>[] = [
  { key: "id", label: "#", width: "8%", align: "num" },
  { key: "agent", label: "에이전트", width: "16%" },
  { key: "model", label: "모델", width: "14%" },
  { key: "ticker", label: "종목", width: "12%" },
  { key: "status", label: "상태", width: "10%", align: "chip", render: (c) => <Chip tone={c.tone}>{statusOf(c.status).text}</Chip> },
  { key: "cost", label: "비용(USD)", width: "14%", align: "num" },
  { key: "duration", label: "소요(초)", width: "12%", align: "num" },
  { key: "turns", label: "턴", width: "8%", align: "num" },
  { key: "created_at", label: "시각", width: "6%", align: "num", render: (c) => fmtWhen(c.created_at) },
];

/** 30행씩 ?page=N — body() 콜백 안에서는 훅을 못 쓰므로 부품으로 뺐다. */
function PagedRuns({ rows, columns }: { rows: RunRow[]; columns: Column<RunRow>[] }) {
  const paged = usePaged(rows);
  return (
    <>
      <Table columns={columns} rows={paged.rows} rowKey={(r) => r.id} empty="이 날짜에 실행 기록 없음" />
      <Pager {...paged} />
    </>
  );
}

export function Runs() {
  const today = useKstDate();
  const [picked, setPicked] = useState<string | null>(null);
  const date = picked ?? today;
  const resetPage = usePageReset();
  const setDate = (v: string) => setPicked(v === today ? null : v);
  const [kind, setKind] = useState("");
  const [run, setRun] = useState<number | null>(null);
  const params = useMemo(() => ({ date, limit: "200", ...(kind ? { kind } : {}) }), [date, kind]);
  const callParams = useMemo(() => (run === null ? undefined : { run_id: String(run) }), [run]);
  const runs = useResource<Run[]>("/runs", params, ["runs"]);
  const jobs = useResource<JobsResp>("/jobs", undefined, ["jobs"]);
  const calls = useResource<CallsResp>("/calls", callParams, ["runs"], { enabled: run !== null });
  const labels = useMemo(() => new Map((jobs.data?.kinds ?? []).map((k) => [k.kind, k.label])), [jobs.data]);

  const columns = useMemo<Column<RunRow>[]>(
    () => [
      {
        key: "id",
        label: "실행 번호",
        width: "8%",
        align: "num",
        render: (r) => (
          <button type="button" className="link-btn" aria-pressed={r.id === run} onClick={() => setRun(r.id)}>
            #{r.id}
          </button>
        ),
      },
      { key: "kind", label: "종류", width: "18%", render: (r) => kindLabel(r.kind, labels) },
      { key: "market", label: "시장", width: "8%" },
      { key: "status", label: "상태", width: "10%", align: "chip", render: (r) => <Chip tone={r.tone}>{r.status}</Chip> },
      { key: "started_at", label: "시작", width: "12%", align: "num", render: (r) => fmtWhen(r.started_at) },
      { key: "duration", label: "소요", width: "10%", align: "num" },
      { key: "error", label: "오류 이력", width: "34%", long: true, render: (r) => <>
        {r.error ?? ""}
        {r.followup_at ? <p className="data-muted">후속 정상 시각 {fmtWhen(r.followup_at)}</p> : null}
        {r.scope_confirmed === false && r.tone === "fail" ? <p className="data-muted">대상 범위 미확인 — 후속 정상 연결 보류</p> : null}
      </> },
    ],
    [run, labels],
  );

  return (
    <>
      <div className="perf-filters">
        <DatePicker
          value={date}
          onChange={(v) => {
            setDate(v);
            setRun(null);
            resetPage();
          }}
        />
        <select
          aria-label="잡 종류"
          value={kind}
          onChange={(e) => {
            setKind(e.target.value);
            setRun(null);
            resetPage();
          }}
        >
          <option value="">전체</option>
          {(jobs.data?.kinds ?? []).map((k) => (
            <option key={k.kind} value={k.kind}>
              {k.label || k.kind}
            </option>
          ))}
        </select>
        <a className="btn" href={csvUrl("/runs", params)}>
          CSV
        </a>
      </div>
      <p className="data-muted">중단은 종료 요청으로 멈춘 이력입니다. 후속 정상은 같은 대상 범위에서 나중에 시작한 실행이 완료된 기록이며, 원인 해결을 보장하지 않습니다. 원문 오류는 그대로 남깁니다.</p>
      {body(runs, "실행 기록", (rs) => (
        <PagedRuns rows={runRows(rs)} columns={columns} />
      ))}
      {run !== null ? (
        <>
          <h3 className="perf-sub">
            run #{run} 호출{" "}
            <a className="btn" href={csvUrl("/calls", { run_id: String(run) })}>
              CSV
            </a>
          </h3>
          {body(calls, "호출", (c) => (
            <Table columns={CALL_COLUMNS} rows={callRows(c.calls)} rowKey={(x) => x.id} empty="호출 없음" />
          ))}
        </>
      ) : null}
    </>
  );
}
