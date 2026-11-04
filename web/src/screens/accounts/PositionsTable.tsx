// 계좌별 보유 표 — 현물·단타(종목·수량·평단·현재가·평가손익·손절·목표, 현물은 lot 펼침)와 파생(코드·종류·방향·계약·평단·현재가·원화·만기/보유기한·상태·손절·목표).
// 현재가는 시세가 오기 전 「—」, 실시간이면 「실시간」 칩, 낡은 시세면 「지연」 칩.
import type { ReactNode } from "react";
import { Chip } from "../../components/Chip";
import { Table, type Column } from "../../components/Table";
import { fmtKrw, fmtMoney, fmtPct, fmtPt } from "../../lib/format";
import { stopLabel, type LiveRow, type PositionRow } from "./positions.ts";

type Row = LiveRow<PositionRow>;
const cur = (r: Row): "KRW" | "USD" => (r.currency === "USD" ? "USD" : "KRW");

function liveChip(r: Row): ReactNode {
  if (r.live) return <Chip tone="live">실시간</Chip>;
  if (r.stale) return <Chip>지연</Chip>;
  return null;
}

function price(r: Row, fmt: (v: number | null) => string): ReactNode {
  if (r.current_price === null) return "—";
  return (
    <>
      {fmt(r.current_price)} {liveChip(r)}
    </>
  );
}

function signed(v: number | null, fmt: (v: number | null) => string): string {
  const s = fmt(v);
  return v !== null && v > 0 ? `+${s}` : s;
}

const key = (r: Row) => `${r.lane}-${r.market}-${r.ticker}`;

export function PositionsTable({
  rows,
  partial,
  empty,
  expand,
}: {
  rows: Row[];
  partial?: boolean;
  empty: string;
  expand?: (row: Row) => ReactNode;
}) {
  const columns: Column<Row>[] = [
    { key: "ticker", label: "종목", width: "22%", long: true, render: (r) => (r.name ? `${r.ticker} ${r.name}` : r.ticker) },
    { key: "qty", label: "수량", width: "8%", align: "num", render: (r) => String(r.qty ?? "—") },
    { key: "avg", label: "평단", width: "11%", align: "num", render: (r) => fmtMoney(r.avg_price, cur(r)) },
    { key: "price", label: "현재가", width: "15%", align: "num", render: (r) => price(r, (v) => fmtMoney(v, cur(r))) },
    {
      key: "pnl",
      label: "평가손익",
      width: "14%",
      align: "num",
      render: (r) => (r.pnl === null ? "—" : `${signed(r.pnl, (v) => fmtMoney(v, cur(r)))} (${fmtPct(r.pnl_pct === null ? null : r.pnl_pct / 100)})`),
    },
    { key: "stop", label: "손절", width: "15%", align: "chip", render: (r) => <Chip>{stopLabel(r.stop, cur(r))}</Chip> },
    { key: "target", label: "목표", width: "15%", align: "chip", render: (r) => <Chip>{stopLabel(r.target, cur(r))}</Chip> },
  ];
  return <Table columns={columns} rows={rows} rowKey={key} partial={partial} empty={empty} expand={expand} />;
}

const KIND: Record<string, string> = { future: "선물", call: "콜", put: "풋" };
const DIRECTION: Record<string, string> = { long: "매수", short: "매도" };

export function FoPositionsTable({ rows, partial }: { rows: Row[]; partial?: boolean }) {
  const columns: Column<Row>[] = [
    { key: "code", label: "코드", width: "11%", long: true, render: (r) => r.ticker },
    { key: "kind", label: "종류", width: "6%", render: (r) => KIND[r.kind ?? ""] ?? r.kind ?? "—" },
    { key: "direction", label: "방향", width: "6%", render: (r) => DIRECTION[r.direction ?? ""] ?? r.direction ?? "—" },
    { key: "qty", label: "계약", width: "6%", align: "num", render: (r) => String(r.qty ?? "—") },
    { key: "avg", label: "평단(pt)", width: "9%", align: "num", render: (r) => fmtPt(r.avg_price) },
    { key: "price", label: "현재가(pt)", width: "12%", align: "num", render: (r) => price(r, fmtPt) },
    { key: "krw", label: "원화", width: "11%", align: "num", render: (r) => fmtKrw(r.krw_value ?? null) },
    { key: "horizon", label: "만기/보유기한", width: "13%", render: (r) => `${r.last_trade_date ?? "—"} / ${r.horizon_until ?? "—"}` },
    { key: "status", label: "상태", width: "6%", align: "chip", render: (r) => <Chip>{r.status ?? "—"}</Chip> },
    { key: "stop", label: "손절", width: "10%", align: "chip", render: (r) => <Chip>{stopLabel(r.stop, "PT")}</Chip> },
    { key: "target", label: "목표", width: "10%", align: "chip", render: (r) => <Chip>{stopLabel(r.target, "PT")}</Chip> },
  ];
  return <Table columns={columns} rows={rows} rowKey={key} partial={partial} empty="파생 포지션 없음" />;
}
