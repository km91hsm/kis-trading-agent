// 데몬 — GET /system/daemon: 살아 있음·pid·가동 시간·하트비트 경과·낡음, 잡 표(마지막 실행·다음 실행).
import { Chip } from "../../components/Chip";
import { Table, type Column } from "../../components/Table";
import { fmtTime } from "../../lib/format";
import { useResource } from "../../lib/hooks";
import { body } from "../performance/ResourceView";

type DaemonJob = {
  id: string;
  kind: string;
  trigger: string;
  schedule_key: string | null;
  interval_s: number | null;
  next_at: string | null;
  last_start: string | null;
  last_end: string | null;
  last_status: string | null;
  last_error: string | null;
};
type DaemonResp = {
  alive: boolean;
  pid: number | null;
  started_at: string | null;
  uptime_s: number | null;
  heartbeat_pid: number | null;
  pid_mismatch: boolean;
  updated_at: string | null;
  age_s: number | null;
  stale: boolean;
  stopped_at: string | null;
  stale_after_s: number;
  jobs: DaemonJob[];
};

function duration(s: number | null): string {
  if (s === null) return "—";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}시간 ${m}분` : `${m}분`;
}

const COLUMNS: Column<DaemonJob>[] = [
  { key: "id", label: "잡", width: "28%", long: true },
  { key: "kind", label: "종류", width: "18%" },
  { key: "last_status", label: "마지막", width: "12%", align: "chip", render: (j) => (j.last_status ? <Chip tone={j.last_status === "ok" ? "ok" : j.last_status === "error" ? "fail" : "muted"}>{j.last_status}</Chip> : "—") },
  { key: "last_end", label: "마지막 끝", width: "14%", align: "num", render: (j) => fmtTime(j.last_end) },
  { key: "next_at", label: "다음 실행", width: "14%", align: "num", render: (j) => fmtTime(j.next_at) },
  { key: "last_error", label: "오류", width: "14%", render: (j) => j.last_error ?? "" },
];

export function Daemon() {
  const res = useResource<DaemonResp>("/system/daemon", undefined, ["daemon", "jobs"]);
  return body(res, "데몬", (d) => (
    <>
      <p>
        <Chip tone={d.alive ? "ok" : "fail"}>{d.alive ? "동작 중" : "꺼짐"}</Chip>{" "}
        {d.stale ? <Chip tone="warn">하트비트 낡음</Chip> : null} {d.pid_mismatch ? <Chip tone="warn">pid 불일치</Chip> : null}{" "}
        <span className="data-muted">
          pid {d.pid ?? "—"} · 가동 {duration(d.uptime_s)} · 하트비트 {d.age_s === null ? "—" : `${Math.round(d.age_s)}초 전`}
        </span>
      </p>
      <Table columns={COLUMNS} rows={d.jobs} rowKey={(j) => j.id} empty="등록된 잡 없음" />
    </>
  ));
}
