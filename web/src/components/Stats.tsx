// 통계 격자 — 라벨 위·값 아래(·부제) 칸을 cols 열로. 「값 · 값 · 값」 문자열 대신 쓰는 한 부품.
// size "sm" 은 본문 크기(절 머리·카드 안), "kpi" 는 큰 숫자(화면 머리). num 이면 값에 tabular-nums.
import type { ReactNode } from "react";

export type Stat = { label: string; value: ReactNode; sub?: ReactNode; num?: boolean; /** 칸 식별자(`data-stat`) */ id?: string };

export function Stats({ items, cols = "auto", size = "sm" }: { items: Stat[]; cols?: 2 | 3 | 4 | "auto"; size?: "sm" | "kpi" }) {
  return (
    <dl className="stats" data-cols={String(cols)} data-size={size}>
      {items.map((s, i) => (
        <div key={s.id ?? `${i}-${s.label}`} className="stat" data-stat={s.id}>
          <dt>{s.label}</dt>
          <dd className={s.num === false ? undefined : "num"}>{s.value}</dd>
          {s.sub !== undefined && s.sub !== null && s.sub !== "" ? <dd className="stat-sub">{s.sub}</dd> : null}
        </div>
      ))}
    </dl>
  );
}
