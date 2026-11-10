export type PolicyMode = "AUTO" | "RISK_ON" | "RISK_OFF";
export type RiskMarket = {
  market: string;
  regime: string | null;
  asof: string | null;
  signals: Record<string, boolean | null>;
  values: Record<string, number | null>;
  streak: number | null;
  exit_streak_days: number | null;
  ai_view: { date?: string | null; stance?: string | null; confidence?: number | null; rationale?: string | null } | null;
  policy: { mode: PolicyMode; source: string; reason: string | null; expires_at: string | null; date: string | null };
  effective: { stance: string; entry: "blocked" | "limited" | "standard" | "unknown"; reason: string; max_position_pct: number | null; max_positions: number | null };
  accounts: { id: string; env: string }[];
  history: { at: string; mode: PolicyMode; reason: string; actor: string | null; expires_at: string | null }[];
};
export type RiskPolicyResponse = { markets: RiskMarket[] };
export const policyNames: Record<PolicyMode, string> = { AUTO: "자동 — 시장 판단 따르기", RISK_ON: "리스크 ON — 제한적 매수", RISK_OFF: "리스크 OFF — 신규 매수 차단" };
export const marketName = (market: string) => market === "KR" ? "국내" : market === "US" ? "미국" : market;
export const stanceName = (stance: string | null | undefined) => ({ RISK_ON: "매수 지향", RISK_OFF: "방어", NEUTRAL: "중립" }[stance ?? ""] ?? "판단 자료 없음");
export const entryName = (entry: RiskMarket["effective"]["entry"]) => ({ blocked: "신규 매수 차단", limited: "제한적 매수", standard: "일반 매수 정책", unknown: "매수 허용 여부 확인 필요" }[entry] ?? "매수 허용 여부 확인 필요");
export function policyLimits(effective: RiskMarket["effective"]) {
  const { max_position_pct: weight, max_positions: positions } = effective;
  return [weight != null ? `종목당 최대 ${weight.toLocaleString("ko-KR", { style: "percent", maximumSignificantDigits: 15 })}` : null, positions != null ? `최대 ${positions}종목` : null].filter(Boolean).join(" · ");
}
/** 시장의 현지 날짜·시각을 표시해 미국 정책의 만료일을 KST 날짜로 오인하지 않게 한다. */
export function policyTime(value: string | null | undefined, market: string): string {
  if (!value) return "자료 없음";
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "시각 확인 필요";
  const timeZone = market === "US" ? "America/New_York" : "Asia/Seoul";
  const parts = new Intl.DateTimeFormat("sv-SE", { timeZone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(date);
  return `${parts} ${market === "US" ? "ET" : "KST"}`;
}
