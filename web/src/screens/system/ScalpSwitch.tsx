// 단타 계좌별 스위치 — 모의는 바로, 실전 켜기는 계좌 id 입력 확인(Confirm). 끄기·기본값으로는 확인 없음.
import { useState } from "react";
import { Chip } from "../../components/Chip";
import { Confirm } from "../../components/Confirm";
import { Table, type Column } from "../../components/Table";
import { errorText, patchJson } from "../../lib/api";
import { useResource } from "../../lib/hooks";
import { body } from "../performance/ResourceView";

type ScalpAccount = {
  id: string; env: "paper" | "real"; markets: string[]; enabled: boolean;
  source: "control" | "yaml" | "default"; effective: boolean; reason: string | null;
};
type ScalpSettings = { accounts: ScalpAccount[]; judge: boolean; max_positions: number; max_weight_pct: number; default_paper: boolean };

const SOURCE: Record<string, string> = { control: "화면에서 설정", yaml: "설정 파일 기본값", default: "실전 기본 꺼짐" };

export function ScalpSwitch() {
  const res = useResource<ScalpSettings>("/settings/scalp", undefined, ["control", "accounts"]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);

  const save = async (id: string, payload: { enabled: boolean | null; confirm?: string }) => {
    setBusy(true);
    setError(null);
    try {
      await patchJson<ScalpSettings>(`/settings/scalp/${encodeURIComponent(id)}`, payload);
      res.reload();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const click = (a: ScalpAccount) => {
    if (a.enabled) void save(a.id, { enabled: false });
    else if (a.env === "real") setPending(a.id);
    else void save(a.id, { enabled: true });
  };

  const columns: Column<ScalpAccount>[] = [
    { key: "id", label: "계좌", width: "22%", render: (a) => <code>{a.id}</code> },
    { key: "env", label: "구분", width: "12%", align: "chip", render: (a) => <Chip tone={a.env === "real" ? "live" : "muted"}>{a.env === "real" ? "실전" : "모의"}</Chip> },
    { key: "sw", label: "단타", width: "24%", action: true, render: (a) => (
      <button type="button" role="switch" aria-label={`${a.id} 단타`} aria-checked={a.enabled} className="btn" disabled={busy}
        onClick={() => click(a)}>{a.enabled ? "켜짐 · 끄기" : "꺼짐 · 켜기"}</button>
    ) },
    { key: "effective", label: "실효", width: "24%", render: (a) => (a.effective ? "동작" : a.reason ?? "꺼짐") },
    { key: "source", label: "출처", width: "18%", render: (a) => (
      <>
        <span className="data-muted">{SOURCE[a.source] ?? a.source}</span>
        {a.source === "control" ? <button type="button" className="btn" disabled={busy} onClick={() => void save(a.id, { enabled: null })}>기본값으로</button> : null}
      </>
    ) },
  ];

  return (
    <div className="scalp-switch">
      <h3 className="perf-sub">단타</h3>
      {body(res, "단타 설정", (s) => (
        <>
          <p className="perf-note">계좌마다 단타를 켜고 끕니다. 실전 계좌는 계좌 id 를 입력해 켜고, 그 계좌가 「자동」 모드이고 LIVE 확인이 된 동안만 돕니다. 상한·조건은 모의와 같습니다(판정자 {s.judge ? "켬" : "끔"}, 최대 보유 {s.max_positions}, 종목당 비중 {s.max_weight_pct}%).</p>
          <Table columns={columns} rows={s.accounts} rowKey={(a) => a.id} empty="계좌 없음" />
        </>
      ))}
      {error ? <p role="alert" className="perf-note">{error}</p> : null}
      <Confirm
        open={pending !== null}
        title="실전 단타 켜기"
        body="이 실전 계좌에서 단타가 실제 주문을 낼 수 있게 됩니다."
        live={pending ? { accountId: pending } : undefined}
        onConfirm={(confirm) => { const id = pending; setPending(null); if (id) void save(id, { enabled: true, confirm }); }}
        onClose={() => setPending(null)}
      />
    </div>
  );
}
