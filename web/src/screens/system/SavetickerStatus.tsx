import { useState } from "react";
import { Chip } from "../../components/Chip";
import { Confirm } from "../../components/Confirm";
import { Stats } from "../../components/Stats";
import { Table, type Column } from "../../components/Table";
import { errorText, postJson } from "../../lib/api";
import { fmtInt, fmtWhen } from "../../lib/format";
import type { CollectionState, SavetickerResp, SavetickerStream } from "./system";
import "./SavetickerStatus.css";

type StatusLabel = { text: string; tone: "ok" | "warn" | "fail" | "muted" };
const STATUS: Record<CollectionState, StatusLabel> = {
  normal: { text: "정상", tone: "ok" },
  waiting: { text: "대기", tone: "muted" },
  login_required: { text: "로그인 필요", tone: "warn" },
  stopped: { text: "중단", tone: "warn" },
  error: { text: "오류", tone: "fail" },
  partial: { text: "일부 수집", tone: "warn" },
  empty: { text: "0건", tone: "muted" },
  delayed: { text: "지연", tone: "warn" },
  unknown: { text: "미확인", tone: "muted" },
};
const BACKEND: Record<string, string> = { extension: "Chrome 확장", playwright: "자동 브라우저(Playwright)" };
const SERVICE: Record<string, string> = {
  running: "실행 중", collecting: "수집 중", stopped: "중단", paused: "일시정지", blocked: "중단",
  backoff: "재시도 대기", error: "오류", failed: "오류", waiting_chrome: "Chrome 연결 대기",
  auth_required: "로그인 필요", waiting_verification: "사이트 확인 필요", logging_in: "로그인 중", reauthenticating: "로그인 복구 중",
};

export function savetickerState(s: SavetickerResp): StatusLabel {
  if (!s.configured) return { text: "설정 안 됨", tone: "muted" };
  const service = s.service?.state;
  if (service === "auth_required" || service === "waiting_verification"
    || (s.blocked && !["restart_limit", "chromium_install_failed"].includes(s.service?.reason ?? ""))) return STATUS.login_required;
  if (s.blocked || service === "stopped" || service === "paused") return STATUS.stopped;
  if (service === "failed" || service === "error") return STATUS.error;
  if (service === "waiting_chrome" || service === "backoff" || service === "logging_in" || service === "reauthenticating") return STATUS.waiting;
  if (s.summary && s.summary.state !== "normal") return STATUS[s.summary.state] ?? STATUS.unknown;
  if (s.health === "fault") return STATUS.error;
  if ((service === "collecting" || service === "running") && (s.health === "healthy" || s.health === "ok")) return { text: "정상 · 수집 중", tone: "ok" };
  return STATUS.unknown;
}

function stamp(value: string | null) {
  return value ? <time dateTime={value} title={value}>{fmtWhen(value)}</time> : <span className="data-muted">미기록</span>;
}

const COLUMNS: Column<SavetickerStream>[] = [
  { key: "label", label: "수집 항목", width: "24%", long: true },
  { key: "state", label: "상태", width: "12%", align: "chip", render: (r) => {
    if (r.scope === "historical") return <Chip>과거 기록</Chip>;
    if (r.scope === "unconfirmed") return <Chip>대상 미확정</Chip>;
    const label = STATUS[r.state] ?? STATUS.unknown;
    return <Chip tone={label.tone}>{label.text}</Chip>;
  } },
  { key: "last_attempt_at", label: "최근 시도", width: "12%", render: (r) => stamp(r.last_attempt_at) },
  { key: "last_response_at", label: "최근 응답", width: "12%", render: (r) => stamp(r.last_response_at) },
  { key: "last_success_at", label: "유효 자료", width: "12%", render: (r) => stamp(r.last_success_at) },
  { key: "last_change_at", label: "값 변화", width: "18%", render: (r) => <span className="collection-change">
    {stamp(r.last_change_at)}
    <span className="data-muted">{{ changed: "값 변경", unchanged: "변화 없음", empty: "0건 응답", unknown: "최근 변화 미확인" }[r.change_state]}</span>
  </span> },
  { key: "count", label: "최근 건수", width: "10%", align: "num", render: (r) => r.count === null ? "미기록" : `${fmtInt(r.count)}건` },
];

export function SavetickerStatus({ s }: { s: SavetickerResp }) {
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const restart = async () => {
    setConfirming(false);
    setBusy(true);
    setError(null);
    try {
      const r = await postJson<{ ok: boolean; note: string | null }>("/system/saveticker/restart", null);
      setNote(r.note || "다시 시작을 요청했습니다 — 데몬이 1분 안에 처리합니다");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const status = savetickerState(s);
  const summary = s.summary;
  return <div className="collection-status">
    <Stats cols={4} items={[
      { label: "상태", value: <Chip tone={status.tone}>{status.text}</Chip>, num: false },
      { label: "서비스", value: s.service?.state ? (SERVICE[s.service.state] ?? "미확인") : "—", sub: s.service?.at ? fmtWhen(s.service.at) : undefined, num: false },
      { label: "수집 방식", value: BACKEND[s.backend] ?? "미확인", num: false },
      { label: "마지막 성공", value: fmtWhen(s.last_success_at) },
    ]} />
    {summary ? <p className="collection-summary">
      <strong>기록된 항목 {summary.recorded}개</strong>
      {summary.active_recorded !== undefined ? <span className="data-muted">점검 대상 기록 {summary.active_recorded}개</span> : null}
      {(Object.keys(STATUS) as CollectionState[]).filter((key) => summary[key] > 0).map((key) => <Chip key={key} tone={STATUS[key].tone}>{STATUS[key].text} {summary[key]}</Chip>)}
      {summary.historical ? <Chip>과거 기록 {summary.historical}</Chip> : null}
      {summary.unconfirmed ? <Chip>대상 미확정 {summary.unconfirmed}</Chip> : null}
    </p> : null}
    {summary?.note ? <p className="data-muted">{summary.note}</p> : null}
    <Table columns={COLUMNS} rows={s.streams ?? []} rowKey={(r) => r.id} empty="수집 기록 없음 · 첫 수집을 기다립니다" expand={(r) => <div className="collection-detail">
      {r.scope && r.scope !== "active" ? <p>{r.scope === "historical" ? "과거 월의 저장 기록입니다." : "현재 수집 대상으로 지정되었는지 저장 기록으로 확인할 수 없습니다."} 현재 전체 수집 상태에는 반영하지 않습니다.</p> : null}
      <p>{r.error ?? (r.state === "empty" ? "최근 응답은 0건입니다. 이전 유효 자료가 남아 있을 수 있습니다." : r.state === "delayed" ? "목표 주기보다 수집 시도가 늦습니다. 값 변화 여부와는 별개입니다." : r.state === "partial" ? "일부 자료만 수집되었습니다." : "최근 수집 요청의 오류 기록은 없습니다.")}</p>
      <p>{r.recovery ?? s.recovery?.text ?? "복구 상태 미기록"}</p>
      <span className="data-muted">항목 ID: {r.id}</span>
    </div>} />
    <details className="collection-guide">
      <summary>시각과 상태 읽는 법</summary>
      <p>응답 성공과 값 변화는 다릅니다. 최근 응답은 부분·0건 응답을 포함하고, 유효 자료는 1건 이상 저장된 마지막 수집입니다.</p>
      <p>값 변화는 최근 변경 이력에서 확인합니다. 미기록은 기록이 없거나 조회 범위 밖이라는 뜻입니다. 정규장 기준 자료도 수집 시도로 상태를 판단하며, 휴장 여부는 이 기록만으로 확정하지 않습니다.</p>
      <p>전체 상태 집계는 상시 점검 대상으로 선언된 항목의 기록을 사용합니다. 과거 달력과 현재 수집 대상이 미확정인 항목은 저장 내역으로만 표시하며 전체 지연 건수에 더하지 않습니다.</p>
    </details>
    {s.reason_text ? <p className="perf-note">{s.reason_text}</p> : null}
    {s.recovery && s.recovery.state !== "none" ? <p className="perf-note">{s.recovery.text}</p> : null}
    <div><button type="button" className="btn" disabled={busy || !s.configured} onClick={() => setConfirming(true)}>다시 시작</button></div>
    {note ? <p className="perf-note" role="status">{note}</p> : null}
    {error ? <p className="data-error" role="alert">{error}</p> : null}
    <Confirm open={confirming} title="데이터 수집 다시 시작" body={<p>수집 서비스를 다시 시작하도록 데몬에 요청합니다. 쌓인 데이터는 그대로입니다.</p>}
      onConfirm={() => void restart()} onClose={() => setConfirming(false)} />
  </div>;
}
