// 칩 — 짧은 꼬리표. live 는 실전 아이콘을 앞에 붙인다. Chips 는 max 개까지 보이고 나머지는 「+N」.
import type { ReactNode } from "react";
import type { Tone } from "./Badge";
import { Icon } from "./Icon";

export function Chip({ tone = "muted", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span className="chip" data-tone={tone}>
      {tone === "live" ? <Icon name="live" /> : null}
      {children}
    </span>
  );
}

export function Chips({ items, max }: { items: string[]; max: number }) {
  const shown = items.slice(0, max);
  const rest = items.slice(max);
  return (
    <span className="chips">
      {shown.map((t, i) => (
        <Chip key={`${i}-${t}`}>{t}</Chip>
      ))}
      {rest.length ? (
        <span className="chip" data-tone="muted" title={rest.join(", ")}>
          +{rest.length}
        </span>
      ) : null}
    </span>
  );
}
