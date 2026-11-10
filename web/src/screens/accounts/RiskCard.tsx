// 리스크 카드(현물 계좌만) — VaR95·CVaR95·베타(Stats), 서버가 설정 한도(limits)를 주면 「한도 대비」 표(항목·현재·한도·판정 —
// 상한은 「최대」, 현금 하한은 「최소」, 손실 한도는 「손실」; 값 없는 줄은 「자료 없음」), 그리고 위험 기여 상위 3종목 막대(코드+이름).
// 자료가 모자라면 서버 사유를 Empty 한 줄로(한도 표는 그래도 그린다).
import { Card } from "../../components/Card";
import { Chip } from "../../components/Chip";
import { Empty } from "../../components/Empty";
import { Stats } from "../../components/Stats";
import { Table, type Column } from "../../components/Table";
import { fmtPct } from "../../lib/format";
import { tickerLabel } from "../../lib/ticker";

export type RiskLimits = {
  max_position_pct: number;
  max_positions: number;
  max_sector_pct: number;
  min_cash_pct: number;
  daily_loss_stop_pct: number;
};

export type RiskUsage = {
  position_pct_max: number | null;
  positions: number | null;
  sector_pct_max: number | null;
  cash_pct: number | null;
  daily_loss_pct: number | null;
};

export type Risk = {
  market?: string;
  insufficient?: string;
  var95?: number | null;
  cvar95?: number | null;
  beta?: number | null;
  contributions?: { ticker: string; weight: number; contribution: number }[];
  limits?: RiskLimits;
  usage?: RiskUsage;
};

export type LimitRow = { label: string; used: number | null; limit: number; kind: "max" | "min" | "loss"; count?: boolean };

export function limitRows(limits: RiskLimits, usage: Partial<RiskUsage> = {}): LimitRow[] {
  return [
    { label: "최대 종목 비중 (헤지·현금 ETF 제외)", used: usage.position_pct_max ?? null, limit: limits.max_position_pct, kind: "max" },
    { label: "보유 종목 수 (헤지·현금 ETF 제외)", used: usage.positions ?? null, limit: limits.max_positions, kind: "max", count: true },
    { label: "섹터 합계 (헤지·현금 ETF 제외)", used: usage.sector_pct_max ?? null, limit: limits.max_sector_pct, kind: "max" },
    { label: "현금 비중 (결제 예정 반영)", used: usage.cash_pct ?? null, limit: limits.min_cash_pct, kind: "min" },
    { label: "일일 손익", used: usage.daily_loss_pct ?? null, limit: limits.daily_loss_stop_pct, kind: "loss" },
  ];
}

/** 한도를 넘었나 — 상한(max)은 초과, 하한(min, 현금)은 미달, 손실 한도(음수)는 그 이하. 사용량을 모르면 false. */
export function breached(r: LimitRow): boolean {
  if (r.used === null) return false;
  if (r.kind === "max") return r.used > r.limit;
  return r.kind === "min" ? r.used < r.limit : r.used <= r.limit;
}

const KIND_WORD = { max: "최대", min: "최소", loss: "손실" } as const;
const BREACH_WORD = { max: "초과", min: "미달", loss: "초과" } as const;

function shown(v: number | null, count?: boolean): string {
  if (v === null) return "—";
  return count ? String(v) : fmtPct(v);
}

function verdict(r: LimitRow) {
  if (r.used === null) return <Chip>자료 없음</Chip>;
  return breached(r) ? <Chip tone="fail">{BREACH_WORD[r.kind]}</Chip> : <Chip tone="ok">정상</Chip>;
}

const LIMIT_COLUMNS: Column<LimitRow>[] = [
  { key: "label", label: "항목", width: "34%", long: true },
  { key: "used", label: "현재", width: "22%", align: "num", render: (r) => shown(r.used, r.count) },
  { key: "limit", label: "한도", width: "24%", align: "num", render: (r) => `${KIND_WORD[r.kind]} ${shown(r.limit, r.count)}` },
  { key: "verdict", label: "판정", width: "20%", align: "chip", render: verdict },
];

function LimitTable({ limits, usage }: { limits: RiskLimits; usage?: RiskUsage }) {
  return (
    <div className="risk-limits">
      <h3 className="risk-limits-title">한도 대비</h3>
      <Table columns={LIMIT_COLUMNS} rows={limitRows(limits, usage)} rowKey={(r) => r.label} />
    </div>
  );
}

export function RiskCard({ risk, names = {} }: { risk: Risk; names?: Record<string, string | null | undefined> }) {
  const title = risk.market ? `리스크 (${risk.market})` : "리스크";
  const limits = risk.limits ? <LimitTable limits={risk.limits} usage={risk.usage} /> : null;
  if (risk.insufficient) {
    return (
      <Card title={title}>
        <Empty title="자료 부족">{risk.insufficient}</Empty>
        {limits}
      </Card>
    );
  }
  const top = (risk.contributions ?? []).slice(0, 3);
  return (
    <Card title={title}>
      <details className="account-detail">
        <summary>전문 위험 지표 · VaR·CVaR·베타</summary>
        <p className="data-muted">과거 가격으로 추정한 손실 범위와 시장 민감도입니다. 미래 손실을 보장하지 않습니다.</p>
        <Stats
          cols={3}
          items={[
            { id: "var95", label: "VaR95", value: fmtPct(risk.var95 ?? null) },
            { id: "cvar95", label: "CVaR95", value: fmtPct(risk.cvar95 ?? null) },
            { id: "beta", label: "베타", value: risk.beta === null || risk.beta === undefined ? "—" : risk.beta.toFixed(2) },
          ]}
        />
      </details>
      {limits}
      {top.length ? (
        <ul className="risk-contrib" aria-label="위험 기여 상위">
          {top.map((c) => (
            <li key={c.ticker}>
              <span className="risk-ticker">{tickerLabel(c.ticker, names[c.ticker])}</span>
              <div className="risk-bar">
                <div className="risk-bar-fill" style={{ width: `${Math.max(0, Math.min(100, c.contribution * 100))}%` }} />
              </div>
              <span className="risk-pct">{fmtPct(c.contribution)}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </Card>
  );
}
