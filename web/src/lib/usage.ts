// 옛 `web/src/usage.ts` 이식
// Claude 사용량 표시: `GET /system/usage` 응답 타입, 사용률 단계, 리셋까지 남은 시간, 상단 띠 칩.

/** 창 하나(5시간·7일·지출 한도). used_pct 는 0~100. 관측 시각은 창마다 따로 붙을 수 있다. */
export type LimitWindow = {
  used_pct: number | null;
  resets_at: string | null;
  observed_at?: string | null;
  age_s?: number | null;
} | null;

/** `session.usage.latest_observation` — 원장·상태줄 중 최신 관측. 둘 다 없으면 null. */
export type RateLimits = {
  observed_at: string | null;
  age_s: number | null;
  five_hour: LimitWindow;
  seven_day: LimitWindow;
  spend_limit: LimitWindow;
  source?: "session" | "probe" | "statusline";
  status?: string | null;
} | null;

export type Throttle = {
  enabled: boolean;
  source: "control" | "yaml";
  soft_pct: number;
  hard_pct: number;
  state: "ok" | "soft" | "hard";
  reason: string | null;
};

/** `session.usage.ledger_windows` 의 5시간·7일 창 집계. */
export type UsageWindow = { sessions: number; cost_usd: number; avg_duration_s: number | null; errors: number };

export type UsageResp = {
  rate_limits: RateLimits;
  throttle: Throttle;
  usage_file_present: boolean;
  windows: { h5: UsageWindow; d7: UsageWindow; today: { sessions: number; cap: number } };
  caps_by_kind: { kind: string; used: number; cap: number | null }[];
  note: string;
};

export type UsageLevel = "ok" | "warn" | "fail";

/** 70% 미만 ok, 90% 미만 warn, 그 밖 fail. */
export function usageLevel(pct: number): UsageLevel {
  if (pct < 70) return "ok";
  if (pct < 90) return "warn";
  return "fail";
}

/** 리셋까지 남은 시간 「30분」·「1시간 20분」·「4일」(분 올림). 지났거나 모르면 "". */
export function resetsIn(resetsAt: string | null | undefined, now: Date): string {
  if (!resetsAt) return "";
  const ms = new Date(resetsAt).getTime() - now.getTime();
  if (!Number.isFinite(ms) || ms <= 0) return "";
  const m = Math.ceil(ms / 60000);
  if (m < 60) return `${m}분`;
  if (m < 48 * 60) return `${Math.floor(m / 60)}시간 ${m % 60}분`;
  return `${Math.round(m / 1440)}일`;
}

/** 상단 띠 칩 「72% · 리셋 1시간 20분」. 관측 없는 창은 0% 가 아니라 「—」(level "none"). */
export function usageChip(window: LimitWindow, now: Date): { text: string; level: UsageLevel | "none" } {
  const pct = window?.used_pct;
  if (pct === null || pct === undefined) return { text: "—", level: "none" };
  const left = resetsIn(window?.resets_at, now);
  return { text: left ? `${Math.round(pct)}% · 리셋 ${left}` : `${Math.round(pct)}%`, level: usageLevel(pct) };
}
