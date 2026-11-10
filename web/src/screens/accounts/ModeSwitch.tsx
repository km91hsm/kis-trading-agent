// 모드 전환 — 선택 + 「적용」 → 확인 대화상자. 실전 계좌는 항상 계좌 id 재입력(→ `confirm`), 모의는 확인만(`confirm` 없음).
import { useState } from "react";
import { Confirm } from "../../components/Confirm";
import { WriteAuthNotice, useWriteAuth } from "../../components/WriteAuthNotice";
import { errorText, patchJson } from "../../lib/api";
import { modeOptions, type Mode } from "./positions.ts";

const MODE_LABEL: Record<string, string> = { auto: "자동", approve: "승인", off: "끔" };

export function ModeSwitch({ id, env, mode, modeNote, onDone }: { id: string; env: string; mode: string; modeNote?: string | null; onDone(): void }) {
  const hasAuth = useWriteAuth();
  const [value, setValue] = useState<string>(mode);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const real = env !== "paper";

  async function submit(confirm?: string) {
    setOpen(false);
    setBusy(true);
    setError(null);
    try {
      await patchJson(`/accounts/${encodeURIComponent(id)}`, confirm ? { mode: value, confirm } : { mode: value });
      onDone();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mode-switch">
      <WriteAuthNotice available={hasAuth} />
      <label>
        모드{" "}
        <select value={value} onChange={(e) => setValue(e.target.value)} disabled={busy || !hasAuth}>
          {modeOptions(env).map((m: Mode) => (
            <option key={m} value={m}>
              {MODE_LABEL[m]}
            </option>
          ))}
        </select>
      </label>{" "}
      <button type="button" className="btn" disabled={busy || !hasAuth || value === mode} onClick={() => setOpen(true)}>
        적용
      </button>
      {modeNote ? <p className="mode-note">{modeNote}</p> : null}
      {error ? (
        <p className="mode-error" role="alert">
          {error}
        </p>
      ) : null}
      <Confirm
        confirmDisabled={!hasAuth}
        open={open}
        title="모드 전환"
        body={
          real && value === "auto"
            ? `${id} 를 「자동」으로 바꿉니다. 실전 자동 매매(LIVE)가 켜집니다.`
            : `${id} 모드를 「${MODE_LABEL[value] ?? value}」(으)로 바꿉니다.`
        }
        live={real ? { accountId: id } : undefined}
        onConfirm={(confirm) => void submit(confirm)}
        onClose={() => setOpen(false)}
      />
    </div>
  );
}
