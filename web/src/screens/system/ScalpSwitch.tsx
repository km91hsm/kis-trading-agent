// 단타 계좌별 스위치 — 모의는 바로, 실전 켜기는 계좌 id 입력 확인(Confirm). 끄기·기본값으로는 확인 없음.
import { useState } from "react";
import { WriteAuthNotice, useWriteAuth } from "../../components/WriteAuthNotice";
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
type ScalpSettings = {
  accounts: ScalpAccount[]; judge: boolean; judge_source?: "control" | "yaml"; judge_default?: boolean;
  max_positions: number; max_weight_pct: number; default_paper: boolean;
};

const SOURCE: Record<string, string> = { control: "화면에서 설정", yaml: "설정 파일 기본값", default: "실전 기본 꺼짐" };

export function ScalpSwitch() {
  const res = useResource<ScalpSettings>("/settings/scalp", undefined, ["control", "accounts"]);
  const hasAuth = useWriteAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [judgeConfirm, setJudgeConfirm] = useState(false);

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
  const saveJudge = async (enabled: boolean | null) => {
    setJudgeConfirm(false);
    setBusy(true);
    setError(null);
    try {
      await patchJson<ScalpSettings>("/settings/scalp-judge", { enabled });
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
      <button type="button" role="switch" aria-label={`${a.id} 단타`} aria-checked={a.enabled} className="btn" disabled={busy || !hasAuth}
        onClick={() => click(a)}>{a.enabled ? "켜짐 · 끄기" : "꺼짐 · 켜기"}</button>
    ) },
    { key: "effective", label: "실효", width: "24%", render: (a) => (a.effective ? "동작" : a.reason ?? "꺼짐") },
    { key: "source", label: "출처", width: "18%", render: (a) => (
      <>
        <span className="data-muted">{SOURCE[a.source] ?? a.source}</span>
        {a.source === "control" ? <button type="button" className="btn" disabled={busy || !hasAuth} onClick={() => void save(a.id, { enabled: null })}>기본값으로</button> : null}
      </>
    ) },
  ];

  return (
    <div className="scalp-switch">
      {body(res, "단타 설정", (s) => (
        <>
          <WriteAuthNotice available={hasAuth} />
          <p className="perf-note">계좌마다 단타를 켜고 끕니다. 실전 계좌는 계좌 id 를 입력해 켜고, 그 계좌가 「자동」 모드이고 LIVE 확인이 된 동안만 돕니다. 상한·조건은 모의와 같습니다(최대 보유 {s.max_positions}, 종목당 비중 {(s.max_weight_pct * 100).toLocaleString("ko-KR", { maximumFractionDigits: 2 })}%).</p>
          <p className="perf-note" aria-label="단타 판정자">
            판정자 <b>{s.judge ? "켜짐" : "꺼짐"}</b> · {s.judge_source === "control" ? "화면에서 설정" : "설정 파일"} — 켜면 신호마다 AI 가 GO/NO_GO 를 판정한 뒤에만 진입합니다.{" "}
            <button type="button" className="btn" disabled={busy || !hasAuth} onClick={() => (s.judge ? void saveJudge(false) : setJudgeConfirm(true))}>{s.judge ? "판정자 끄기" : "판정자 켜기"}</button>
            {s.judge_source === "control" ? <>{" "}<button type="button" className="btn" disabled={busy || !hasAuth} onClick={() => void saveJudge(null)}>판정자 기본값으로</button></> : null}
          </p>
          <Table columns={columns} rows={s.accounts} rowKey={(a) => a.id} empty="계좌 없음" />
        </>
      ))}
      {error ? <p role="alert" className="perf-note">{error}</p> : null}
      <Confirm
        confirmDisabled={!hasAuth}
        open={judgeConfirm}
        title="단타 판정자 켜기"
        body="단타 신호마다 AI 판정(GO/NO_GO)을 거친 뒤에만 진입합니다. 신호마다 Claude 호출이 생겨 구독 사용량이 늘고, 사용률이 높으면 감속에 걸려 판정이 늦어질 수 있습니다."
        onConfirm={() => void saveJudge(true)}
        onClose={() => setJudgeConfirm(false)}
      />
      <Confirm
        confirmDisabled={!hasAuth}
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
