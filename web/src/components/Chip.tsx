// 칩 — 짧은 꼬리표. live 는 실전 아이콘을 앞에 붙인다. Chips 는 max 개까지 보이고 나머지는 「+N」.
import type { HTMLAttributes, ReactNode } from "react";
import type { Tone } from "./Badge";
import { Icon } from "./Icon";

type ChipProps = { tone?: Tone; children: ReactNode } & Omit<HTMLAttributes<HTMLSpanElement>, "children"> & { "data-draft"?: string };

export function Chip({ tone = "muted", children, ...rest }: ChipProps) {
  return (
    <span className="chip" data-tone={tone} {...rest}>
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
