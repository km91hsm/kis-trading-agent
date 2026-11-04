// 도움말 말풍선 — 마우스 올림·키보드 포커스 때 CSS 로 보인다. 대상에 aria-describedby 로 묶는다.
import { useId, type ReactNode } from "react";

export function Tooltip({ text, children }: { text: string; children: ReactNode }) {
  const id = useId();
  return (
    <span className="tooltip" aria-describedby={id} tabIndex={0}>
      {children}
      <span role="tooltip" id={id} className="tooltip-body">
        {text}
      </span>
    </span>
  );
}
