// 빈 상태 — 아직 데이터가 없거나 화면이 준비 중일 때 한 줄 제목 + 선택 설명.
import type { ReactNode } from "react";

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {children ? <div className="empty-body">{children}</div> : null}
    </div>
  );
}
