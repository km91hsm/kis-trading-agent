// 테스트 응답 고정값 — 화면 테스트들이 함께 쓴다. 기본값은 "조용한 정상" 상태, 필요한 칸만 덮어쓴다.
import type { Health } from "../lib/health";
import type { UsageResp } from "../lib/usage";

export function fixtureUsage(
  over: { five_h_pct?: number; seven_d_pct?: number; five_h_resets_at?: string; seven_d_resets_at?: string } = {},
): UsageResp {
  return {
    rate_limits: {
      observed_at: "2026-11-05T10:00:00+09:00",
      age_s: 0,
      five_hour: { used_pct: over.five_h_pct ?? 10, resets_at: over.five_h_resets_at ?? null },
      seven_day: { used_pct: over.seven_d_pct ?? 5, resets_at: over.seven_d_resets_at ?? null },
      spend_limit: null,
      source: "statusline",
    },
    throttle: { enabled: true, source: "yaml", soft_pct: 70, hard_pct: 90, state: "ok", reason: null },
    usage_file_present: true,
    windows: {
      h5: { sessions: 0, cost_usd: 0, avg_duration_s: null, errors: 0 },
      d7: { sessions: 0, cost_usd: 0, avg_duration_s: null, errors: 0 },
      today: { sessions: 0, cap: 40 },
    },
    caps_by_kind: [],
    note: "",
  };
}

export function fixtureHealth(over: Partial<Health> = {}): Health {
  return { level: "ok", reasons: [], checked_at: "2026-11-05T10:00:00+09:00", ...over };
}
