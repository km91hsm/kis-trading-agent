// `GET /system/health` 표시: 상태 점 단계와 상단 띠 한 줄 요약.

export type Health = {
  level: "ok" | "warn" | "fail";
  reasons: { area: string; level: string; text: string }[];
  checked_at: string;
  version?: string;
  features?: string[];
};

export type HealthLevel = Health["level"];

/** 점 색 단계. 모르는 값(응답 전·info 등)은 warn. */
export function healthDot(level: string | null | undefined): HealthLevel {
  return level === "ok" || level === "fail" || level === "warn" ? level : "warn";
}

const LEVEL_TEXT: Record<HealthLevel, string> = { ok: "정상", warn: "주의", fail: "장애" };

/** 「<전체 단계와 같은 첫 사유의 첫 줄> · <시계 어긋남>」. 정상이면 「정상」, 응답 전이면 「상태 확인 중」. */
export function healthSummary(h: Health | null, skew: string | null): string {
  let head: string;
  if (!h) head = "상태 확인 중";
  else if (h.level === "ok") head = "정상";
  else head = (h.reasons.find((r) => r.level === h.level) ?? h.reasons[0])?.text.split("\n")[0] || LEVEL_TEXT[h.level];
  return skew ? `${head} · ${skew}` : head;
}
