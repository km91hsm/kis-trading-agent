// 설정 요약 — GET /settings 를 세 묶음(AI 사용 한도 · 거래 리스크 · 접속 환경)의 Stats 로, 역할별 모델 표는 details 로 접는다(역할은 glossary role.*).
import { Stats } from "../../components/Stats";
import { Table, type Column } from "../../components/Table";
import { GLOSSARY } from "../../lib/glossary";
import { useResource } from "../../lib/hooks";
import { body } from "../performance/ResourceView";
import { CodexDebate } from "./CodexDebate";

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
  { key: "role", label: "역할", width: "50%", render: (r) => GLOSSARY[`role.${r.role}`]?.label ?? r.role },
  { key: "model", label: "모델", width: "50%", render: (r) => <code>{r.model}</code> },
];

export function SettingsPanel() {
  const res = useResource<SettingsResp>("/settings", undefined, ["control"]);
  return body(res, "설정", (s) => {
    const models = Object.entries(s.models).map(([role, model]) => ({ role, model }));
    const pct = (key: string) => typeof s.risk?.[key] === "number" && Number.isFinite(s.risk[key])
      ? (s.risk[key] as number).toLocaleString("ko-KR", { style: "percent", maximumSignificantDigits: 15 }) : "미기록";
    return (
      <>
        <CodexDebate />
        <h3 className="perf-sub">AI 사용 한도</h3>
        <Stats
          cols={4}
          items={[
            { label: "하루 세션 상한", value: s.llm.daily_session_cap === null ? "—" : `${s.llm.daily_session_cap}회` },
            { label: "LLM 예산", value: s.llm.budget_usd === null ? "—" : `$${s.llm.budget_usd}` },
            { label: "토론 라운드", value: `${s.debate_rounds}회` },
          ]}
        />
        <h3 className="perf-sub">거래 리스크</h3>
        <Stats
          cols={4}
          items={[
            { label: "시장", value: s.markets.join(", ") || "—", num: false },
            { label: "종목당 최대 비중", value: pct("max_position_pct") },
            { label: "최대 보유 종목", value: typeof s.risk?.max_positions === "number" ? `${s.risk.max_positions}개` : "미기록" },
            { label: "하루 손실 중단", value: pct("daily_loss_stop_pct") },
            { label: "기본 손절", value: pct("stop_loss_pct") },
            { label: "한 번의 손실 한도", value: pct("risk_per_trade_pct") },
            { label: "업종 최대 비중", value: pct("max_sector_pct") },
            { label: "최소 현금 비중", value: pct("min_cash_pct") },
          ]}
        />
        <p className="perf-note">설정된 기본 한도입니다. 계좌별 적용값·주문 허용 여부는 보유 상태와 리스크 검사 결과에 따라 달라집니다.</p>
        <h3 className="perf-sub">접속 환경</h3>
        <Stats
          cols={4}
          items={[
            { label: "웹", value: `${s.web.bind}:${s.web.port}` },
            { label: "Tailscale 호스트", value: String(s.web.tailscale_hosts_count) },
            { label: "Tailscale 사용자", value: String(s.web.tailscale_users_count) },
          ]}
        />
        <details className="sys-details">
          <summary>역할별 모델 {models.length}</summary>
          <Table columns={MODEL_COLUMNS} rows={models} rowKey={(r) => r.role} empty="모델 설정 없음" />
        </details>
      </>
    );
  });
}
