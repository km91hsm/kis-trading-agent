import { expect, test } from "vitest";
import { NOISY_KEYS, SSE_KEYS } from "./sse-keys";

test("SSE_KEYS 는 daemon 을 포함하고 중복이 없다", () => {
  expect(Array.isArray(SSE_KEYS)).toBe(true);
  expect(SSE_KEYS).toContain("daemon");
  expect(new Set(SSE_KEYS).size).toBe(SSE_KEYS.length);
});

test("NOISY_KEYS 는 SSE_KEYS 의 부분집합이다", () => {
  const all: readonly string[] = SSE_KEYS;
  for (const k of NOISY_KEYS) expect(all).toContain(k);
});
