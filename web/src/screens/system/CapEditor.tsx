// 하루 제한 한 줄 + 「제한 변경」(숫자 입력 → 저장 / 기본값으로). Claude 하루 세션 제한과 Codex 하루 호출
// 제한이 함께 쓴다. 저장은 부모가 넘긴 onSave(cap | null — null 이면 설정 파일 값으로 되돌림).
import { useId, useState } from "react";
import { errorText } from "../../lib/api";

export type CapSource = "control" | "yaml" | string | undefined;

export function CapEditor({ label, value, source, defaultValue, min, max, unit, onSave }: {
  label: string;
  value: number;
  source: CapSource;
  defaultValue?: number;
  min: number;
  max: number;
  unit: string;
  onSave: (cap: number | null) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(String(value));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const hintId = useId();
  const parsed = Number(draft);
  const valid = Number.isInteger(parsed) && parsed >= min && parsed <= max;
  const save = async (cap: number | null) => {
    setBusy(true);
    setError(null);
    try {
      await onSave(cap);
      setEditing(false);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return <div className="perf-note" aria-label={label}>
    <span>{label} <b className="num">{value}{unit}</b> · {source === "control" ? "화면 설정" : "설정 파일"}</span>{" "}
    {editing ? <form className="cap-editor" onSubmit={(e) => { e.preventDefault(); if (valid) void save(parsed); }}>
      <input type="number" inputMode="numeric" min={min} max={max} step={1} value={draft} aria-label={`${label} 새 값`} aria-invalid={!valid} aria-describedby={hintId} onChange={(e) => setDraft(e.target.value)} />
      <button type="submit" className="btn btn-primary" disabled={busy || !valid}>저장</button>
      <button type="button" className="btn" disabled={busy} title={defaultValue !== undefined ? `설정 파일 값 ${defaultValue}${unit}` : "설정 파일 값"} onClick={() => void save(null)}>기본값으로</button>
      <button type="button" className="btn" disabled={busy} onClick={() => { setEditing(false); setError(null); }}>취소</button>
      <span id={hintId} className={valid ? "data-muted" : "data-error"}>{min}~{max} 사이 정수</span>
    </form> : <button type="button" className="btn" onClick={() => { setDraft(String(value)); setEditing(true); }}>제한 변경</button>}
    {error ? <p className="data-error" role="alert">{error}</p> : null}
  </div>;
}
