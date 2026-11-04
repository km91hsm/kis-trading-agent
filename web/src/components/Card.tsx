// 카드 — 제목·오른쪽 부가 영역(선택)과 본문.
import type { ReactNode } from "react";

export function Card({ title, right, children }: { title?: string; right?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      {title || right ? (
        <header className="card-head">
          {title ? <h3 className="card-title">{title}</h3> : <span />}
          {right}
        </header>
      ) : null}
      {children}
    </section>
  );
}
