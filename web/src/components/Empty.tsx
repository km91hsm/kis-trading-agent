// 빈 상태 — 아직 데이터가 없거나 화면이 준비 중일 때 한 줄 제목 + 선택 설명.
import type { ReactNode } from "react";

export function Empty({ title, children, onRetry, retrying = false, helpHref, helpLabel = "운영 상태 확인" }: {
  title: string; children?: ReactNode; onRetry?: () => void; retrying?: boolean; helpHref?: string; helpLabel?: string;
}) {
  return (
    <div className="empty" role={onRetry ? "alert" : undefined}>
      <p className="empty-title">{title}</p>
      {children ? <div className="empty-body">{children}</div> : null}
      {onRetry || helpHref ? <div className="empty-actions">
        {onRetry ? <button type="button" className="btn" disabled={retrying} onClick={onRetry}>{retrying ? "다시 조회 중…" : "다시 조회"}</button> : null}
        {helpHref ? <a href={helpHref}>{helpLabel}</a> : null}
      </div> : null}
    </div>
  );
}
