// 확인 대화상자 — role="dialog" aria-modal, Esc 닫기, Tab 포커스 트랩, 닫히면 연 곳으로 포커스 복귀.
// live 가 있으면 계좌 id 를 똑같이 입력해야 확인 버튼이 켜지고 onConfirm(accountId), 없으면 onConfirm(undefined).
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

const FOCUSABLE = 'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])';

export type ConfirmProps = {
  open: boolean;
  title: string;
  body: ReactNode;
  live?: { accountId: string };
  onConfirm(confirm?: string): void;
  onClose(): void;
};

export function Confirm(props: ConfirmProps) {
  return props.open ? createPortal(<ConfirmDialog {...props} />, document.body) : null;
}

function ConfirmDialog({ title, body, live, onConfirm, onClose }: ConfirmProps) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const inputId = useId();
  const [typed, setTyped] = useState("");
  const ready = !live || typed === live.accountId;
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>(FOCUSABLE)?.focus();
    const onKey = (e: KeyboardEvent) => {
      const el = ref.current;
      if (!el) return;
      if (e.key === "Escape") {
        e.preventDefault();
        closeRef.current();
        return;
      }
      if (e.key !== "Tab") return;
      const items = Array.from(el.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement as HTMLElement | null;
      const inside = !!active && el.contains(active);
      if (e.shiftKey && (active === first || !inside)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (active === last || !inside)) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      if (prev && prev.isConnected) prev.focus();
    };
  }, []);

  return (
    <div
      className="confirm-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div ref={ref} className="confirm" role="dialog" aria-modal="true" aria-labelledby={titleId} data-live={live ? "" : undefined}>
        <h2 id={titleId} className="confirm-title">
          {title}
        </h2>
        <div className="confirm-body">{body}</div>
        {live ? (
          <div className="confirm-live">
            <label htmlFor={inputId}>
              실전 계좌입니다. 확인하려면 계좌 id <code>{live.accountId}</code> 를 그대로 입력하세요.
            </label>
            <input id={inputId} type="text" autoComplete="off" spellCheck={false} value={typed} onChange={(e) => setTyped(e.target.value)} />
          </div>
        ) : null}
        <div className="confirm-actions">
          <button type="button" className="btn" onClick={onClose}>
            취소
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={!ready}
            onClick={() => {
              if (ready) onConfirm(live ? live.accountId : undefined);
            }}
          >
            확인
          </button>
        </div>
      </div>
    </div>
  );
}
