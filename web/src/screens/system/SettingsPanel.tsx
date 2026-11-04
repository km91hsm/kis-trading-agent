// 설정 요약 — GET /settings: 시장·토론 라운드·역할별 모델·웹 바인드·Tailscale 허용 수·LLM 예산.
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

export function SettingsPanel() {
  const res = useResource<SettingsResp>("/settings", undefined, ["control"]);
  return body(res, "설정", (s) => {
    const rows: [string, string][] = [
      ["시장", s.markets.join(", ") || "—"],
      ["토론 라운드", String(s.debate_rounds)],
      ...Object.entries(s.models).map(([role, model]): [string, string] => [`모델 · ${role}`, model]),
      ["웹", `${s.web.bind}:${s.web.port}`],
      ["Tailscale 호스트", String(s.web.tailscale_hosts_count)],
      ["Tailscale 사용자", String(s.web.tailscale_users_count)],
      ["LLM 예산(USD)", s.llm.budget_usd === null ? "—" : String(s.llm.budget_usd)],
      ["하루 세션 상한", s.llm.daily_session_cap === null ? "—" : String(s.llm.daily_session_cap)],
      ["리스크 설정", s.risk ? "있음" : "없음"],
    ];
    return (
      <dl className="sys-dl">
        {rows.map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
    );
  });
}
