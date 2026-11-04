// 잡 실행 버튼 — 누르면 Confirm, 확인하면 POST /jobs {kind, params, confirm?}. 응답 id 를 onDone 으로.
// 실전 대상(targetAccountId 가 실전 계좌, 또는 대상 없음 + trading + 실전 계좌 있음)이면 대화상자에
// 실전 계좌 <select> 를 넣고, 고른 계좌 id 를 Confirm live 로 넘겨 그대로 재입력해야 확인이 켜진다(스펙 §5 실전 확인 = 재입력).
// 그 값을 confirm 으로 보낸다. 모의·비매매는 confirm 키 자체를 넣지 않는다.
// 실패(409 데몬 없음·중복, 400, 401/403 등)는 버튼 아래 한 줄. 401/403 은 api.ts 가 전역 안내도 띄운다.
import { useEffect, useId, useRef, useState } from "react";
import { ApiError, postJson } from "../lib/api";
import { Confirm } from "./Confirm";
import { Icon } from "./Icon";

type JobResp = { id: number; kind: string; params: Record<string, unknown>; status: string; label: string };

export type RunJobButtonProps = {
  kind: string;
  params?: Record<string, unknown>;
  label?: string;
  trading: boolean;
  realAccounts: string[];
  targetAccountId?: string;
  onDone?(reqId: number): void;
};

export function RunJobButton({ kind, params, label, trading, realAccounts, targetAccountId, onDone }: RunJobButtonProps) {
  const name = label ?? kind;
  const options = targetAccountId
    ? realAccounts.includes(targetAccountId)
      ? [targetAccountId]
      : []
    : trading
      ? realAccounts
      : [];
  const real = options.length > 0;
  // 실전 대상이 정해져 있으면 그 id 를 미리 고르고, 아니면 사용자가 직접 골라야 확인이 켜진다.
  const preset = targetAccountId && real ? targetAccountId : "";
  const [open, setOpen] = useState(false);
  const [choice, setChoice] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const selectId = useId();
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const start = () => {
    setChoice(preset);
    setError(null);
    setOpen(true);
  };

  const run = async () => {
    const confirm = choice;
    if (real && !confirm) return;
    setOpen(false);
    setBusy(true);
    setError(null);
    const body: Record<string, unknown> = { kind, params: params ?? {} };
    if (real) body.confirm = confirm;
    try {
      const res = await postJson<JobResp>("/jobs", body);
      if (alive.current) onDone?.(res.id);
    } catch (e) {
      if (alive.current) setError(e instanceof ApiError ? e.message : "잡을 실행하지 못했습니다");
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  return (
    <div className="run-job">
      <button type="button" className="btn" disabled={busy} onClick={start}>
        <Icon name="run" />
        {name}
      </button>
      {error ? (
        <p className="run-job-error" role="alert">
          {error}
        </p>
      ) : null}
      <Confirm
        open={open}
        title={`${name} 실행`}
        body={
          real ? (
            <div className="run-job-real">
              <p>실전 계좌로 실행합니다. 계좌를 확인하세요.</p>
              <label htmlFor={selectId}>실전 계좌</label>
              <select id={selectId} value={choice} onChange={(e) => setChoice(e.target.value)}>
                {preset ? null : (
                  <option value="" disabled>
                    계좌 선택
                  </option>
                )}
                {options.map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <p>잡을 대기열에 넣습니다.</p>
          )
        }
        live={real && choice ? { accountId: choice } : undefined}
        confirmDisabled={real && !choice}
        onConfirm={() => void run()}
        onClose={() => setOpen(false)}
      />
    </div>
  );
}
