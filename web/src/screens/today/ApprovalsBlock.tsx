// 대기 승인 — 행마다 근거 요약·승인·거절. 실전(env≠paper)은 계좌 id 재입력 뒤 confirm 을 싣는다.
// 승인 202 는 데몬이 집행할 때까지 「집행 대기」 칩(SSE approvals·orders 로 다시 읽어 행이 사라지면 같이 사라짐).
import { useState } from "react";
import { Badge } from "../../components/Badge";
import { Chip } from "../../components/Chip";
import { Confirm } from "../../components/Confirm";
import { ApiError, patchJson } from "../../lib/api";
import { fmtTime } from "../../lib/format";

export type Approval = {
  id: number;
  account_id: string;
  env: string;
  decision: { asset: string; id: number | null } | null;
  ticker: string;
  side: string;
  qty: number;
  amount: string | null;
  summary: string | null;
  expires_at: string;
  status: string;
  decided_by?: string | null;
  decided_channel?: string | null;
};

type Action = "approve" | "reject";
const ACTION_LABEL: Record<Action, string> = { approve: "승인", reject: "거절" };
const SIDE_LABEL: Record<string, string> = { BUY: "매수", SELL: "매도" };

/** 오류 본문에서 사람이 읽을 문구: detail 이 객체면 그 `error`, 문자열이면 그대로. */
export function errorText(e: unknown): string {
  if (!(e instanceof ApiError)) return String((e as Error)?.message ?? e);
  try {
    const d = JSON.parse(e.message) as { error?: unknown };
    if (d && typeof d.error === "string") return d.error;
  } catch {
    /* 문자열 detail */
  }
  return e.message;
}

export function ApprovalsBlock({ items, onDone }: { items: Approval[]; onDone(): void }) {
  const [asking, setAsking] = useState<{ row: Approval; action: Action } | null>(null);
  const [accepted, setAccepted] = useState<Record<number, number | string>>({});
  const [errors, setErrors] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<number | null>(null);

  async function decide(row: Approval, action: Action, confirm?: string) {
    setAsking(null);
    setBusy(row.id);
    setErrors((m) => {
      const next = { ...m };
      delete next[row.id];
      return next;
    });
    try {
      const body = confirm ? { action, confirm } : { action };
      const res = await patchJson<{ request_id?: number | string }>(`/approvals/${row.id}`, body);
      if (action === "approve") setAccepted((m) => ({ ...m, [row.id]: res?.request_id ?? "" }));
      onDone();
    } catch (e) {
      setErrors((m) => ({ ...m, [row.id]: errorText(e) }));
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <ul className="approvals">
        {items.map((row) => {
          const real = row.env !== "paper";
          const waiting = row.id in accepted || !!row.decided_channel;
          return (
            <li key={row.id} className="approval">
              <div className="approval-head">
                {real ? <Badge tone="live">실전</Badge> : <Badge tone="muted">모의</Badge>}
                <strong>{row.ticker}</strong>
                <span>
                  {SIDE_LABEL[row.side] ?? row.side} {row.qty}주
                </span>
                <span className="approval-meta">
                  {row.account_id} · {fmtTime(row.expires_at)} 만료
                </span>
                {waiting ? (
                  <span title={accepted[row.id] ? `요청 #${accepted[row.id]}` : undefined}>
                    <Chip tone="warn">집행 대기</Chip>
                  </span>
                ) : null}
              </div>
              <p className="approval-summary">{row.summary || "근거 요약 없음"}</p>
              {errors[row.id] ? (
                <p className="approval-error" role="alert">
                  {errors[row.id]}
                </p>
              ) : null}
              <div className="approval-actions">
                <button type="button" className="btn btn-primary" disabled={waiting || busy === row.id} onClick={() => setAsking({ row, action: "approve" })}>
                  승인
                </button>
                <button type="button" className="btn" disabled={waiting || busy === row.id} onClick={() => setAsking({ row, action: "reject" })}>
                  거절
                </button>
              </div>
            </li>
          );
        })}
      </ul>
      <Confirm
        open={!!asking}
        title={asking ? `${asking.row.ticker} ${ACTION_LABEL[asking.action]}` : ""}
        body={asking ? `${asking.row.account_id} · ${SIDE_LABEL[asking.row.side] ?? asking.row.side} ${asking.row.qty}주를 ${ACTION_LABEL[asking.action]}합니다.` : null}
        live={asking && asking.row.env !== "paper" ? { accountId: asking.row.account_id } : undefined}
        onConfirm={(confirm) => asking && void decide(asking.row, asking.action, confirm)}
        onClose={() => setAsking(null)}
      />
    </>
  );
}
