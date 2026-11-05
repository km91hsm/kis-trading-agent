// 설정 요약 — GET /settings: Stats(시장·토론 라운드·웹·Tailscale·LLM 예산·세션 상한·리스크 설정) + 역할별 모델 표.
import { Chip } from "../../components/Chip";
import { Stats } from "../../components/Stats";
import { Table, type Column } from "../../components/Table";
import { termLabel } from "../../lib/glossary";
import { useResource } from "../../lib/hooks";
import { body } from "../performance/ResourceView";

type SettingsResp = {
  markets: string[];
  risk: Record<string, unknown> | null;
  debate_rounds: number;
  models: Record<string, string>;
  web: { bind: string; port: number; tailscale_hosts_count: number; tailscale_users_count: number };
  llm: { budget_usd: number | null; daily_session_cap: number | null };
};
type ModelRow = { role: string; model: string };

const MODEL_COLUMNS: Column<ModelRow>[] = [
  { key: "role", label: "역할", width: "50%", render: (r) => termLabel(`role.${r.role}`) },
  { key: "model", label: "모델", width: "50%", render: (r) => <code>{r.model}</code> },
];

export function SettingsPanel() {
  const res = useResource<SettingsResp>("/settings", undefined, ["control"]);
  return body(res, "설정", (s) => (
    <>
      <Stats
        cols={4}
        items={[
          { label: "시장", value: s.markets.join(", ") || "—", num: false },
          { label: "토론 라운드", value: String(s.debate_rounds) },
          { label: "웹", value: `${s.web.bind}:${s.web.port}` },
          { label: "Tailscale 호스트", value: String(s.web.tailscale_hosts_count) },
          { label: "Tailscale 사용자", value: String(s.web.tailscale_users_count) },
          { label: "LLM 예산(USD)", value: s.llm.budget_usd === null ? "—" : String(s.llm.budget_usd) },
          { label: "하루 세션 상한", value: s.llm.daily_session_cap === null ? "—" : String(s.llm.daily_session_cap) },
          { label: "리스크 설정", value: <Chip tone={s.risk ? "ok" : "muted"}>{s.risk ? "있음" : "없음"}</Chip>, num: false },
        ]}
      />
      <h3 className="perf-sub">역할별 모델</h3>
      <Table
        columns={MODEL_COLUMNS}
        rows={Object.entries(s.models).map(([role, model]) => ({ role, model }))}
        rowKey={(r) => r.role}
        empty="모델 설정 없음"
      />
    </>
  ));
}
