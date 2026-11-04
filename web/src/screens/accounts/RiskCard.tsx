// 리스크 카드(현물 계좌만) — VaR95·CVaR95·베타, 위험 기여 상위 3종목 막대. 자료가 모자라면 서버 사유 한 줄.
import { Card } from "../../components/Card";
import { fmtPct } from "../../lib/format";

export type Risk = {
  market?: string;
  insufficient?: string;
  var95?: number | null;
  cvar95?: number | null;
  beta?: number | null;
  contributions?: { ticker: string; weight: number; contribution: number }[];
};

export function RiskCard({ risk }: { risk: Risk }) {
  const title = risk.market ? `리스크 (${risk.market})` : "리스크";
  if (risk.insufficient) {
    return (
      <Card title={title}>
        <p className="risk-insufficient">자료 부족: {risk.insufficient}</p>
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
      {top.length ? (
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
      ) : null}
    </Card>
  );
}
