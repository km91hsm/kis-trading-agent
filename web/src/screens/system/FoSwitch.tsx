// 파생 계좌별 스위치(단타 ScalpSwitch 와 같은 모양) — 모의만 켤 수 있고 켜기는 확인 창(자동 주문 시작), 끄기·기본값으로는 바로.
// 실전 파생 계좌는 항상 꺼짐이라 스위치 없이 사유만 보인다. 끄면 신규 진입만 멈추고 보유분 감시·축소는 계속.
import { useState } from "react";
import { WriteAuthNotice, useWriteAuth } from "../../components/WriteAuthNotice";
import { Chip } from "../../components/Chip";
import { Confirm } from "../../components/Confirm";
import { Table, type Column } from "../../components/Table";
import { errorText, patchJson } from "../../lib/api";
import { useResource } from "../../lib/hooks";
import { body } from "../performance/ResourceView";

type FoAccount = {
  id: string; env: "paper" | "real"; enabled: boolean;
  source: "control" | "yaml" | "default"; effective: boolean; reason: string | null;
};
type FoSettings = { accounts: FoAccount[]; default_paper: boolean; product: string; vehicles: string[]; max_contracts: number };

const SOURCE: Record<string, string> = { control: "화면에서 설정", yaml: "설정 파일 기본값", default: "실전 기본 꺼짐" };

export function FoSwitch() {
  const res = useResource<FoSettings>("/settings/fo", undefined, ["control", "accounts"]);
  const hasAuth = useWriteAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);

  const save = async (id: string, enabled: boolean | null) => {
    setBusy(true);
    setError(null);
    try {
      await patchJson<FoSettings>(`/settings/fo/${encodeURIComponent(id)}`, { enabled });
      res.reload();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const columns: Column<FoAccount>[] = [
    { key: "id", label: "계좌", width: "22%", render: (a) => <code>{a.id}</code> },
    { key: "env", label: "구분", width: "12%", align: "chip", render: (a) => <Chip tone={a.env === "real" ? "live" : "muted"}>{a.env === "real" ? "실전" : "모의"}</Chip> },
    { key: "sw", label: "파생", width: "24%", action: true, render: (a) => (a.env === "real" ? <span className="data-muted">—</span> : (
      <button type="button" role="switch" aria-label={`${a.id} 파생`} aria-checked={a.enabled} className="btn" disabled={busy || !hasAuth}
        onClick={() => (a.enabled ? void save(a.id, false) : setPending(a.id))}>{a.enabled ? "켜짐 · 끄기" : "꺼짐 · 켜기"}</button>
    )) },
    { key: "effective", label: "실효", width: "24%", render: (a) => (a.effective ? "동작" : a.reason ?? "꺼짐") },
    { key: "source", label: "출처", width: "18%", render: (a) => (
      <>
        <span className="data-muted">{SOURCE[a.source] ?? a.source}</span>
        {a.source === "control" ? <button type="button" className="btn" disabled={busy || !hasAuth} onClick={() => void save(a.id, null)}>기본값으로</button> : null}
      </>
    ) },
  ];

  return (
    <div className="scalp-switch" aria-label="파생 계좌별 스위치">
      {body(res, "파생 설정", (s) => (
        <>
          <WriteAuthNotice available={hasAuth} />
          <p className="perf-note">계좌마다 파생(코스피200 {s.product === "mini" ? "미니" : "일반"} 선물·옵션) 신규 진입을 켜고 끕니다. 모의 계좌 전용이며 현물처럼 별도 검증 없이 바로 매매합니다. 끄면 새 진입만 멈추고 보유분 감시·축소는 계속합니다(최대 {s.max_contracts}계약).</p>
          <Table columns={columns} rows={s.accounts} rowKey={(a) => a.id} empty="파생 계좌 없음" />
        </>
      ))}
      {error ? <p role="alert" className="perf-note">{error}</p> : null}
      <Confirm
        confirmDisabled={!hasAuth}
        open={pending !== null}
        title="파생 켜기"
        body="이 모의 파생 계좌에서 아침 판정 세션과 자동 신규 진입(선물·옵션)이 시작됩니다."
        onConfirm={() => { const id = pending; setPending(null); if (id) void save(id, true); }}
        onClose={() => setPending(null)}
      />
    </div>
  );
}
