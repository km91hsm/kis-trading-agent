// 시스템 「파생」 절의 주문 다리 — 파생 계좌마다 `GET /fo/legs?account_id=`(그날 만든 다리 + 살아 있는 다리) 표.
// 열: 다리(#id 의도 R라운드 · 주문번호) · 상태 칩(glossary leg.*) · 수량(체결/주문) · 가격(시장가/지정가) · 시각.
import { Chip } from "../../components/Chip";
import { Empty } from "../../components/Empty";
import { Table, type Column } from "../../components/Table";
import { fmtPt, fmtWhen } from "../../lib/format";
import { termLabel } from "../../lib/glossary";
import { useResource } from "../../lib/hooks";

export type FoLeg = {
  id: number;
  position_id: number;
  code: string;
  intent: string;
  round: number;
  attempt: number;
  status: string;
  order_no: string | null;
  qty: number;
  filled_qty: number | null;
  price: string | number | null;
  order_type: string | null;
  sent_at: string | null;
  created_at: string;
};

const LIVE = new Set(["prepared", "sending", "accepted", "unknown"]);
const legTone = (s: string): "ok" | "fail" | "warn" | "muted" =>
  s === "filled" ? "ok" : s === "rejected" || s === "cancelled" || s === "expired" ? "fail" : LIVE.has(s) ? "warn" : "muted";

const COLUMNS: Column<FoLeg>[] = [
  {
    key: "leg", label: "다리", width: "34%", long: true,
    render: (l) => `#${l.id} ${termLabel(`fo.intent.${l.intent}`)} R${l.round} · ${l.code}${l.order_no ? ` · ${l.order_no}` : ""}`,
  },
  { key: "status", label: "상태", width: "14%", align: "chip", render: (l) => <Chip tone={legTone(l.status)}>{termLabel(`leg.${l.status}`)}</Chip> },
  { key: "qty", label: "수량", width: "14%", align: "num", render: (l) => `${l.filled_qty ?? 0}/${l.qty}` },
  { key: "price", label: "가격", width: "18%", align: "num", render: (l) => (l.price === null || l.price === undefined ? "시장가" : fmtPt(l.price)) },
  { key: "at", label: "시각", width: "20%", align: "num", render: (l) => fmtWhen(l.sent_at ?? l.created_at) },
];  // fmt: skip

function AccountLegs({ id }: { id: string }) {
  const res = useResource<FoLeg[]>("/fo/legs", { account_id: id }, ["fo", "orders"]);
  return (
    <div className="fo-legs" data-legs={id}>
      <h3 className="fo-legs-title">{id} 주문 다리</h3>
      {res.error ? (
        <Empty title="다리를 불러오지 못했습니다">{res.error.message}</Empty>
      ) : !res.data ? (
        <Empty title="불러오는 중" />
      ) : (
        <Table columns={COLUMNS} rows={res.data} rowKey={(l) => l.id} empty="오늘 주문 다리 없음" />
      )}
    </div>
  );
}

export function FoLegs({ accounts }: { accounts: { id: string }[] }) {
  return (
    <>
      {accounts.map((a) => (
        <AccountLegs key={a.id} id={a.id} />
      ))}
    </>
  );
}
