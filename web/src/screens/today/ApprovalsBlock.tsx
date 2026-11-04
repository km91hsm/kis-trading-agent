// 대기 승인 — 행마다 근거 요약·승인·거절. 실전(env≠paper)은 계좌 id 재입력 뒤 confirm 을 싣는다.
// 행·확인 본문에 금액(계좌 통화 — /accounts currency, 모르면 원화)과 결정 상세 링크(스펙 §6.1-1).
// 승인 202 는 데몬이 집행할 때까지 「집행 대기」 칩(SSE approvals·orders 로 다시 읽어 행이 사라지면 같이 사라짐).
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Badge } from "../../components/Badge";
import { Chip } from "../../components/Chip";
import { Confirm } from "../../components/Confirm";
import { errorText, patchJson } from "../../lib/api";
import { fmtMoney, fmtTime } from "../../lib/format";
import { decisionHref, type Account } from "./today.ts";

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

/** 승인 금액 — 계좌 통화로 「210,000원」·「$461.00」. 금액이 없으면 null. */
function amountText(row: Approval, accounts: Account[]): string | null {
  if (row.amount == null) return null;
  const usd = accounts.find((a) => a.id === row.account_id)?.currency === "USD";
  return usd ? `$${fmtMoney(row.amount, "USD")}` : `${fmtMoney(row.amount, "KRW")}원`;
}

function hrefOf(row: Approval): string | null {
  const d = row.decision;
  return d && d.id != null ? decisionHref({ asset: d.asset, id: d.id, lane: "spot" }) : null;
}

export function ApprovalsBlock({ items, accounts = [], onDone }: { items: Approval[]; accounts?: Account[]; onDone(): void }) {
  const [asking, setAsking] = useState<{ row: Approval; action: Action } | null>(null);
  const [accepted, setAccepted] = useState<Record<number, number | string>>({});
  // 목록에서 사라진 승인은 「집행 대기」 표시를 지운다(같은 id 가 나중에 다시 와도 버튼이 영구히 꺼져 있지 않게).
  useEffect(() => {
    const live = new Set(items.map((r) => r.id));
    setAccepted((m) => {
      const keep = Object.entries(m).filter(([k]) => live.has(Number(k)));
      return keep.length === Object.keys(m).length ? m : Object.fromEntries(keep);
    });
  }, [items]);
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
          const amount = amountText(row, accounts);
          const href = hrefOf(row);
          return (
            <li key={row.id} className="approval">
              <div className="approval-head">
                {real ? <Badge tone="live">실전</Badge> : <Badge tone="muted">모의</Badge>}
                <strong>{row.ticker}</strong>
                <span>
                  {SIDE_LABEL[row.side] ?? row.side} {row.qty}주
                </span>
                {amount ? <span className="approval-amount">{amount}</span> : null}
                <span className="approval-meta">
                  {row.account_id} · {fmtTime(row.expires_at)} 만료
                </span>
                {waiting ? (
                  <span title={accepted[row.id] ? `요청 #${accepted[row.id]}` : undefined}>
                    <Chip tone="warn">집행 대기</Chip>
                  </span>
                ) : null}
              </div>
              <p className="approval-summary">
                {row.summary || "근거 요약 없음"}
                {href ? (
                  <>
                    {" "}
                    <Link to={href}>근거 전문 ›</Link>
                  </>
                ) : null}
              </p>
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
        body={asking ? confirmBody(asking.row, asking.action, accounts) : null}
        live={asking && asking.row.env !== "paper" ? { accountId: asking.row.account_id } : undefined}
        onConfirm={(confirm) => asking && void decide(asking.row, asking.action, confirm)}
        onClose={() => setAsking(null)}
      />
    </>
  );
}

function confirmBody(row: Approval, action: Action, accounts: Account[]): string {
  const amount = amountText(row, accounts);
  return `${row.account_id} · ${SIDE_LABEL[row.side] ?? row.side} ${row.qty}주${amount ? ` · ${amount}` : ""}을(를) ${ACTION_LABEL[action]}합니다.`;
}
