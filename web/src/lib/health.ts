// `GET /system/health` 표시: 상태 점 단계와 상단 띠 한 줄 요약, 영역 한글 라벨.

export type HealthReason = { area: string; level: string; text: string; key?: string; link?: string | null; count?: number | null };

export type Health = {
  level: "ok" | "warn" | "fail";
  /** 영역별 단계(ok·info·warn·fail), 점검 순서. 옛 응답에는 없을 수 있다. */
  areas?: Record<string, string>;
  reasons: HealthReason[];
  checked_at: string;
  version?: string;
  features?: string[];
};

export type HealthLevel = Health["level"];

/** 점 색 단계. 모르는 값(응답 전·info 등)은 warn. */
export function healthDot(level: string | null | undefined): HealthLevel {
  return level === "ok" || level === "fail" || level === "warn" ? level : "warn";
}

export const LEVEL_TEXT: Record<HealthLevel, string> = { ok: "정상", warn: "주의", fail: "장애" };

/** 영역 단계 글귀 — info(관심)는 점 색과 달리 따로 보인다. */
export function areaLevelText(level: string | null | undefined): string {
  if (level === "info") return "관심";
  return LEVEL_TEXT[healthDot(level)];
}

/** 점검 영역 키(커널 `ops/areas.py`·기능 `health_areas`) → 한글. 모르는 키는 그대로. */
export const AREA_LABEL: Record<string, string> = {
  jobs: "잡",
  llm: "LLM",
  orders: "주문",
  daemon: "데몬",
  ledger: "원장",
  accounts: "계좌",
  quotes: "시세",
  usage: "사용량",
  regime: "국면",
  saveticker: "저장소",
  scalp: "단타",
  fo: "파생",
  ingest: "수집",
  evaluation: "평가",
  spot: "현물",
  scan: "스캔",
  ml: "모델",
  briefing: "브리핑",
  news: "뉴스",
  telegram: "텔레그램",
};
export const areaLabel = (key: string): string => AREA_LABEL[key] ?? key;

/** 「<전체 단계와 같은 첫 사유의 첫 줄> · <시계 어긋남>」. 정상이면 「정상」, 응답 전이면 「상태 확인 중」. */
export function healthSummary(h: Health | null, skew: string | null): string {
  let head: string;
  if (!h) head = "상태 확인 중";
  else if (h.level === "ok") head = "정상";
  else head = (h.reasons.find((r) => r.level === h.level) ?? h.reasons[0])?.text.split("\n")[0] || LEVEL_TEXT[h.level];
  return skew ? `${head} · ${skew}` : head;
}
