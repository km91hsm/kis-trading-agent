// 성과 개요 — 필터(계좌·시장·기간), 지표 카드(수익률·MDD·Sortino·회전율·수렴률), NAV 차트(+벤치마크 선), 판정 분포 칩.
import { useMemo } from "react";
import { Card } from "../../components/Card";
import { Chart } from "../../components/Chart";
import { Chip } from "../../components/Chip";
import { Empty } from "../../components/Empty";
import { fmtPct } from "../../lib/format";
import { navSeries } from "../accounts/positions.ts";
import { actionLabel } from "../decision/decision.ts";
import { benchmarkLines, type Days, type FilterAccount, type Filters, type Perf } from "./performance.ts";

const PERIODS: Days[] = ["30", "90", "365"];

export function FilterBar({ accounts, filters, onChange }: { accounts: FilterAccount[]; filters: Filters; onChange(next: Partial<Filters>): void }) {
  const markets = accounts.find((a) => a.id === filters.account_id)?.markets ?? [];
  return (
    <div className="perf-filters">
      <select
        aria-label="계좌"
        value={filters.account_id}
        onChange={(e) => {
          const a = accounts.find((x) => x.id === e.target.value);
          onChange({ account_id: e.target.value, market: a?.markets?.[0] ?? "" });
        }}
      >
        {accounts.map((a) => (
          <option key={a.id} value={a.id}>
            {a.id}
          </option>
        ))}
      </select>
      <select aria-label="시장" value={filters.market} onChange={(e) => onChange({ market: e.target.value })}>
        {markets.map((m) => (
          <option key={m} value={m}>
            {m}
          </option>
        ))}
      </select>
      <span className="perf-periods">
        {PERIODS.map((d) => (
          <button key={d} type="button" className="btn" aria-pressed={filters.days === d} onClick={() => onChange({ days: d })}>
            {d}일
          </button>
        ))}
      </span>
    </div>
  );
}

export function OverviewBody({ perf }: { perf: Perf }) {
  const nav = useMemo(() => navSeries(perf), [perf]);
  const extra = useMemo(() => benchmarkLines(perf).map((b) => ({ label: b.label, data: b.series })), [perf]);
  const m = perf.metrics;
  const stats: [string, string][] = [
    ["수익률", fmtPct(m?.return ?? null)],
    ["MDD", fmtPct(m?.mdd ?? null)],
    ["Sortino", m?.sortino === null || m?.sortino === undefined ? "—" : m.sortino.toFixed(2)],
    ["회전율", fmtPct(perf.turnover)],
    ["수렴률", fmtPct(perf.convergence_rate)],
  ];
  const dist = Object.entries(perf.decision_distribution ?? {});
  return (
    <>
      <div className="perf-stats">
        {stats.map(([label, value]) => (
          <Card key={label} title={label}>
            <p className="perf-stat-value">{value}</p>
          </Card>
        ))}
      </div>
      {nav.length ? <Chart series={nav} extra={extra} /> : <Empty title="NAV 기록 없음" />}
      {extra.length ? <p className="perf-legend">벤치마크: {extra.map((x) => x.label).join(" · ")}</p> : null}
      <p className="perf-dist">
        판정 분포{" "}
        <span className="chips">
          {dist.length ? (
            dist.map(([a, n]) => (
              <Chip key={a}>
                {actionLabel(a.toUpperCase())} {n}
              </Chip>
            ))
          ) : (
            <Chip>판정 없음</Chip>
          )}
        </span>
      </p>
    </>
  );
}
