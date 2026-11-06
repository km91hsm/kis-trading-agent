// 「리스크 게이트」 절 — 그날 결정들의 계좌별 판정 표(시각·계좌·종목(결정 상세 링크)·결과 칩 통과/거부·수량 조정·사유).
import { Link } from "react-router-dom";
import { Chip } from "../../components/Chip";
import { Empty } from "../../components/Empty";
import { Table, type Column } from "../../components/Table";
import type { ApiError } from "../../lib/api";
import { termLabel } from "../../lib/glossary";
import { riskCheckRows, type RiskCheck, type RiskCheckRow } from "./morning.ts";

const COLUMNS: Column<RiskCheckRow>[] = [
  { key: "when", label: "시각", width: "10%", align: "num" },
  { key: "account_id", label: "계좌", width: "14%" },
  {
    key: "ticker", label: "종목", width: "22%", long: true,
    render: (r) => (
      <>
        {r.market ? <><Chip>{r.market}</Chip> </> : null}
        {r.href ? <Link to={r.href}>{r.ticker}</Link> : r.ticker}
      </>
    ),
  },
  { key: "approved", label: "결과", width: "10%", align: "chip", render: (r) => <Chip tone={r.approved ? "ok" : "fail"}>{termLabel(r.approved ? "gate.pass" : "gate.reject")}</Chip> },
  { key: "adjusted", label: "수량 조정", width: "10%", align: "num" },
  { key: "reasons", label: "사유", width: "34%", long: true },
];  // fmt: skip

export function RiskChecks({ data, error }: { data: RiskCheck[] | null | undefined; error: ApiError | null | undefined }) {
  if (error) return <Empty title="리스크 게이트를 불러오지 못했습니다">{error.message}</Empty>;
  if (!data) return <Empty title="불러오는 중" />;
  return <Table columns={COLUMNS} rows={riskCheckRows(data)} rowKey={(r) => r.id} empty="이 날 게이트 판정이 없습니다" />;
}
