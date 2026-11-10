// 데몬 — GET /system/daemon: 머리 Stats(상태·pid·시작·마지막 박동·멈춤), 잡 표(라벨 + 시장 칩·상태·마지막 끝·소요·다음 실행·일정·오류),
// 내부 틱 잡(job_queue·telegram_poll …)은 details 로 접는다. 잡 라벨은 GET /jobs 의 kind 라벨.
import { useMemo } from "react";
import { Chip } from "../../components/Chip";
import { Stats } from "../../components/Stats";
import { Table, type Column } from "../../components/Table";
import { agoText, fmtWhen } from "../../lib/format";
import { useNow, useResource } from "../../lib/hooks";
import { body } from "../performance/ResourceView";
import { daemonRows, type DaemonJob, type DaemonRow, type JobsResp } from "./system.ts";

import { JobActivity } from "./JobActivity";

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

function uptime(s: number | null): string {
  if (s === null) return "—";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}시간 ${m}분` : `${m}분`;
}

function columns(withError: boolean): Column<DaemonRow>[] {
  const cols: Column<DaemonRow>[] = [
    {
      key: "label",
      label: "잡",
      width: "26%",
      long: true,
      render: (j) => (
        <>
          {j.label}
          {j.market ? (
            <>
              {" "}
              <Chip>{j.market}</Chip>
            </>
          ) : null}
        </>
      ),
    },
    { key: "status", label: "마지막", width: "10%", align: "chip", render: (j) => (j.status === "—" ? "—" : <Chip tone={j.tone}>{j.status}</Chip>) },
    { key: "last_end", label: "마지막 끝", width: "14%", align: "num", render: (j) => fmtWhen(j.last_end) },
    { key: "duration", label: "소요", width: "10%", align: "num" },
    { key: "next_at", label: "다음 실행", width: "14%", align: "num", render: (j) => fmtWhen(j.next_at) },
    { key: "schedule", label: "일정", width: withError ? "12%" : "26%", render: (j) => <code>{j.schedule}</code> },
  ];
  if (withError) cols.push({ key: "error", label: "오류", width: "14%", render: (j) => j.error ?? "" });
  return cols;
}

export function Daemon() {
  const res = useResource<DaemonResp>("/system/daemon", undefined, ["daemon", "jobs"]);
  const jobs = useResource<JobsResp>("/jobs", undefined, ["jobs"]);
  const now = useNow(1000);
  const labels = useMemo(() => new Map((jobs.data?.kinds ?? []).map((k) => [k.kind, k.label])), [jobs.data]);
  return body(res, "데몬", (d) => {
    const rows = daemonRows(d.jobs, labels);
    const userCols = columns(rows.user.some((r) => r.error));
    const internalCols = columns(rows.internal.some((r) => r.error));
    return (
      <>
        <Stats
          cols={4}
          items={[
            {
              label: "상태",
              value: (
                <>
                  <Chip tone={d.alive ? "ok" : "fail"}>{d.alive ? "동작 중" : "꺼짐"}</Chip>
                  {d.stale ? <Chip tone="warn">하트비트 낡음</Chip> : null}
                  {d.pid_mismatch ? <Chip tone="warn">pid 불일치</Chip> : null}
                </>
              ),
              num: false,
            },
            { label: "pid", value: d.pid ?? "—", sub: d.uptime_s === null ? undefined : `가동 ${uptime(d.uptime_s)}` },
            { label: "시작", value: fmtWhen(d.started_at) },
            d.alive
              ? { label: "마지막 박동", value: d.age_s === null ? "—" : `${Math.round(d.age_s)}초 전`, sub: fmtWhen(d.updated_at) }
              : { label: "멈춤", value: fmtWhen(d.stopped_at), sub: d.updated_at ? `마지막 박동 ${agoText(d.updated_at, now)}` : undefined },
          ]}
        />
        <JobActivity compact />
        <Table columns={userCols} rows={rows.user} rowKey={(j) => j.id} empty="등록된 잡 없음" />
        {rows.internal.length ? (
          <details className="sys-details">
            <summary>내부 잡 {rows.internal.length}</summary>
            <Table columns={internalCols} rows={rows.internal} rowKey={(j) => j.id} />
          </details>
        ) : null}
      </>
    );
  });
}
