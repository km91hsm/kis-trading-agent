// 자동 생성 파일 — 수정 금지. `uv run python -m trader.kernel.web.gen` 로 다시 만든다.
export const SSE_KEYS = ["daemon"] as const;
export type SseKey = (typeof SSE_KEYS)[number];
export const NOISY_KEYS: SseKey[] = ["daemon"];
