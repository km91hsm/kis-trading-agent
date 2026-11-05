// 리스크 카드(현물 계좌만) — VaR95·CVaR95·베타. 서버가 설정 한도(limits)를 주면 「한도 대비」 막대(사용량/한도, 초과는 강조),
// 없으면 위험 기여 상위 3종목 막대. 자료가 모자라면 서버 사유 한 줄(한도 막대는 그래도 그린다).
import { Card } from "../../components/Card";
import { fmtPct } from "../../lib/format";

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

type LimitRow = { label: string; used: number | null; limit: number; kind: "max" | "min" | "loss"; count?: boolean };

function limitRows(limits: RiskLimits, usage: Partial<RiskUsage> = {}): LimitRow[] {
  return [
    { label: "최대 종목 비중", used: usage.position_pct_max ?? null, limit: limits.max_position_pct, kind: "max" },
    { label: "보유 종목 수", used: usage.positions ?? null, limit: limits.max_positions, kind: "max", count: true },
    { label: "섹터 합계", used: usage.sector_pct_max ?? null, limit: limits.max_sector_pct, kind: "max" },
    { label: "현금 비중", used: usage.cash_pct ?? null, limit: limits.min_cash_pct, kind: "min" },
    { label: "일일 손익", used: usage.daily_loss_pct ?? null, limit: limits.daily_loss_stop_pct, kind: "loss" },
  ];
}

/** 한도를 넘었나 — 상한(max)은 초과, 하한(min, 현금)은 미달, 손실 한도(음수)는 그 이하. 사용량을 모르면 false. */
function breached(r: LimitRow): boolean {
  if (r.used === null) return false;
  if (r.kind === "max") return r.used > r.limit;
  return r.kind === "min" ? r.used < r.limit : r.used <= r.limit;
}

/** 막대 채움(0~1) — 사용량/한도. 현금 하한 0 이면 늘 충족(가득), 손실은 손실일 때만 한도 쪽으로 찬다. */
function fill(r: LimitRow): number {
  if (r.used === null) return 0;
  let ratio: number;
  if (r.kind === "min") ratio = r.limit > 0 ? r.used / r.limit : 1;
  else if (r.kind === "loss") ratio = r.used < 0 && r.limit < 0 ? r.used / r.limit : 0;
  else ratio = r.limit > 0 ? r.used / r.limit : 0;
  return Math.max(0, Math.min(1, ratio));
}

function shown(v: number | null, count?: boolean): string {
  if (v === null) return "—";
  return count ? String(v) : fmtPct(v);
}

function LimitBars({ limits, usage }: { limits: RiskLimits; usage?: RiskUsage }) {
  return (
    <>
      <h3 className="risk-limits-title">한도 대비</h3>
      <ul className="risk-limits">
        {limitRows(limits, usage).map((r) => (
          <li key={r.label} className={breached(r) ? "risk-limit over" : "risk-limit"}>
            <span className="risk-ticker">{r.label}</span>
            <div className="risk-bar">
              <div className="risk-bar-fill" style={{ width: `${fill(r) * 100}%` }} />
            </div>
            <span className="risk-pct">
              {shown(r.used, r.count)} / {shown(r.limit, r.count)}
            </span>
          </li>
        ))}
      </ul>
    </>
  );
}

export function RiskCard({ risk }: { risk: Risk }) {
  const title = risk.market ? `리스크 (${risk.market})` : "리스크";
  const limits = risk.limits ? <LimitBars limits={risk.limits} usage={risk.usage} /> : null;
  if (risk.insufficient) {
    return (
      <Card title={title}>
        <p className="risk-insufficient">자료 부족: {risk.insufficient}</p>
        {limits}
      </Card>
    );
  }
  const top = (risk.contributions ?? []).slice(0, 3);
  return (
    <Card title={title}>
      <dl className="risk-stats">
        <div>
          <dt>VaR95</dt>
          <dd>{fmtPct(risk.var95 ?? null)}</dd>
        </div>
        <div>
          <dt>CVaR95</dt>
          <dd>{fmtPct(risk.cvar95 ?? null)}</dd>
        </div>
        <div>
          <dt>베타</dt>
          <dd>{risk.beta === null || risk.beta === undefined ? "—" : risk.beta.toFixed(2)}</dd>
        </div>
      </dl>
      {limits ??
        (top.length ? (
          <ul className="risk-contrib">
            {top.map((c) => (
              <li key={c.ticker}>
                <span className="risk-ticker">{c.ticker}</span>
                <div className="risk-bar">
                  <div className="risk-bar-fill" style={{ width: `${Math.max(0, Math.min(100, c.contribution * 100))}%` }} />
                </div>
                <span className="risk-pct">{fmtPct(c.contribution)}</span>
              </li>
            ))}
          </ul>
        ) : null)}
    </Card>
  );
}
