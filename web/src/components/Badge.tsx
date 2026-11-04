// 상태 배지 — 색과 함께 글자로 뜻을 전한다(색만으로 전하지 않기).
import type { ReactNode } from "react";

export type Tone = "ok" | "warn" | "fail" | "live" | "muted";

export function Badge({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className="badge" data-tone={tone}>
      {children}
    </span>
  );
}
