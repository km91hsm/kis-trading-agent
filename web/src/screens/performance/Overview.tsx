// 성과 개요 — 필터(계좌·시장·기간), 지표 카드(수익률·MDD·Sortino·회전율·수렴률), NAV 차트(+벤치마크 선), 판정 분포 칩.
import { useMemo } from "react";
import { Chart } from "../../components/Chart";
import { Chip } from "../../components/Chip";
import { Empty } from "../../components/Empty";
import { Stats, type Stat } from "../../components/Stats";
import { fmtPct } from "../../lib/format";
import { navSeries } from "../accounts/positions.ts";
import { actionLabel } from "../decision/decision.ts";
import { benchmarkLines, normalizeNav, type Days, type FilterAccount, type Filters, type Perf } from "./performance.ts";

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
  const extra = useMemo(() => benchmarkLines(perf).map((b) => ({ label: b.label, data: b.series })), [perf]);
  // 벤치마크는 시작=1.0 지수라 NAV(금액)도 같은 기준으로 정규화해 겹친다. 벤치마크가 없으면 금액 그대로.
  const nav = useMemo(() => (extra.length ? normalizeNav(navSeries(perf)) : navSeries(perf)), [perf, extra]);
  const m = perf.metrics;
  // 값이 없으면 「—」 아래 이유(산출 자료 부족) — NAV 이력·만기 결정이 모자라 못 구한 것.
  const stat = (label: string, v: number | null | undefined, fmt: (x: number) => string): Stat =>
    v === null || v === undefined ? { label, value: "—", sub: "산출 자료 부족" } : { label, value: fmt(v) };
  const stats: Stat[] = [
    stat("수익률", m?.return, fmtPct),
    stat("최대 낙폭(MDD)", m?.mdd, fmtPct),
    stat("Sortino", m?.sortino, (x) => x.toFixed(2)),
    stat("회전율", perf.turnover, fmtPct),
    stat("토론 수렴율", perf.convergence_rate, fmtPct),
  ];
  const dist = Object.entries(perf.decision_distribution ?? {});
  return (
    <>
      <Stats items={stats} cols="auto" size="kpi" />
      {nav.length ? <Chart series={nav} extra={extra} /> : <Empty title="NAV 기록 없음" />}
      {extra.length ? <p className="perf-legend">NAV·벤치마크 (시작=1.0): {extra.map((x) => x.label).join(" · ")}</p> : null}
      {dist.length ? (
        <p className="perf-dist">
          판정 분포{" "}
          <span className="chips">
            {dist.map(([a, n]) => (
              <Chip key={a}>
                {actionLabel(a.toUpperCase())} {n}
              </Chip>
            ))}
          </span>
        </p>
      ) : null}
    </>
  );
}
