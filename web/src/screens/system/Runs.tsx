// runs·calls — 날짜(DatePicker)·kind 로 GET /runs(+ CSV 링크), run 번호를 누르면 GET /calls?run_id=(비용·소요·턴, + CSV).
import { useMemo, useState } from "react";
import { Chip } from "../../components/Chip";
import { DatePicker } from "../../components/DatePicker";
import { Table, type Column } from "../../components/Table";
import { csvUrl } from "../../lib/api";
import { now } from "../../lib/clock";
import { fmtTime, kstDate } from "../../lib/format";
import { useResource } from "../../lib/hooks";
import { body } from "../performance/ResourceView";
import { callRows, runRows, type CallRow, type CallsResp, type JobsResp, type Run, type RunRow } from "./system.ts";

const CALL_COLUMNS: Column<CallRow>[] = [
  { key: "id", label: "#", width: "8%", align: "num" },
  { key: "agent", label: "에이전트", width: "16%" },
  { key: "model", label: "모델", width: "14%" },
  { key: "ticker", label: "종목", width: "12%" },
  { key: "status", label: "상태", width: "10%", align: "chip", render: (c) => <Chip tone={c.tone}>{c.status}</Chip> },
  { key: "cost", label: "비용(USD)", width: "14%", align: "num" },
  { key: "duration", label: "소요(초)", width: "12%", align: "num" },
  { key: "turns", label: "턴", width: "8%", align: "num" },
  { key: "created_at", label: "시각", width: "6%", align: "num", render: (c) => fmtTime(c.created_at) },
];

export function Runs() {
  const [date, setDate] = useState(() => kstDate(now()));
  const [kind, setKind] = useState("");
  const [run, setRun] = useState<number | null>(null);
  const params = useMemo(() => ({ date, limit: "100", ...(kind ? { kind } : {}) }), [date, kind]);
  const callParams = useMemo(() => (run === null ? undefined : { run_id: String(run) }), [run]);
  const runs = useResource<Run[]>("/runs", params, ["runs"]);
  const jobs = useResource<JobsResp>("/jobs", undefined, ["jobs"]);
  const calls = useResource<CallsResp>("/calls", callParams, ["runs"], { enabled: run !== null });

  const columns = useMemo<Column<RunRow>[]>(
    () => [
      {
        key: "id",
        label: "run",
        width: "10%",
        align: "num",
        render: (r) => (
          <button type="button" className="btn" aria-pressed={r.id === run} onClick={() => setRun(r.id)}>
            {r.id}
          </button>
        ),
      },
      { key: "kind", label: "종류", width: "20%" },
      { key: "market", label: "시장", width: "8%" },
      { key: "status", label: "상태", width: "10%", align: "chip", render: (r) => <Chip tone={r.tone}>{r.status}</Chip> },
      { key: "started_at", label: "시작", width: "12%", align: "num", render: (r) => fmtTime(r.started_at) },
      { key: "duration", label: "소요(초)", width: "10%", align: "num" },
      { key: "error", label: "오류", width: "30%", long: true, render: (r) => r.error ?? "" },
    ],
    [run],
  );

  return (
    <>
      <div className="perf-filters">
        <DatePicker
          value={date}
          onChange={(v) => {
            setDate(v);
            setRun(null);
          }}
        />
        <select
          aria-label="잡 종류"
          value={kind}
          onChange={(e) => {
            setKind(e.target.value);
            setRun(null);
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
      {body(runs, "실행 기록", (rs) => (
        <Table columns={columns} rows={runRows(rs)} rowKey={(r) => r.id} empty="이 날짜에 실행 기록 없음" />
      ))}
      {run !== null ? (
        <>
          <h3 className="perf-sub">
            run {run} 호출{" "}
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
