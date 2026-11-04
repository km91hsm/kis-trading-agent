// 「성과」 화면 — `/performance`. 필터(계좌·시장·기간 30/90/365) → 개요(지표 카드·NAV+벤치마크·판정 분포),
// 계좌×시장 비교, 보정, 섀도 기준선, RL, 파생 scorecard(fo 계좌가 있을 때만), 복기 메모리(종목 검색 → q).
// 절 컨테이너는 데이터 전에 바로 그리고(메뉴 앵커 performance-overview·memory), 절마다 로딩·오류를 따로 보인다.
import { useMemo, useState, type ReactNode } from "react";
import { Card } from "../../components/Card";
import { Empty } from "../../components/Empty";
import { fmtKrw, fmtPct } from "../../lib/format";
import { useResource, type Resource } from "../../lib/hooks";
import { CalibrationTable } from "./CalibrationTable";
import { CompareTable } from "./CompareTable";
import { MemoryBlock } from "./MemoryBlock";
import { FilterBar, OverviewBody } from "./Overview";
import { defaultFilters, type Calibration, type Compare, type FilterAccount, type Filters, type Perf, type Shadow } from "./performance.ts";
import { body, failed } from "./ResourceView";
import { RlExperimentsTable, RlSummary, ShadowTables, type RlExperiments, type RlReport } from "./ShadowTables";

type FoScorecard = { decisions: number; matured: number; hit_rate: number | null; contract_pnl: string | number | null };

export function Performance() {
  const accounts = useResource<FilterAccount[]>("/accounts", undefined, ["accounts"]);
  const list = accounts.data ?? [];
  const [over, setOver] = useState<Partial<Filters>>({});
  const f: Filters = { ...defaultFilters(list), ...over };
  const chosen = !!f.account_id;
  const hasFo = list.some((a) => a.lane === "fo");

  const perfParams = useMemo(() => ({ account_id: f.account_id, market: f.market, days: f.days }), [f.account_id, f.market, f.days]);
  const daysParams = useMemo(() => ({ days: f.days }), [f.days]);
  const marketParams = useMemo(() => ({ market: f.market }), [f.market]);
  const rlParams = useMemo(() => ({ account_id: f.account_id, market: f.market }), [f.account_id, f.market]);
  const calibParams = useMemo(() => ({ limit: "20" }), []);

  const perf = useResource<Perf>("/analytics/performance", perfParams, ["market_views", "accounts", "orders"], { enabled: chosen });
  const compare = useResource<Compare>("/analytics/performance/compare", daysParams, ["accounts", "orders"]);
  const calib = useResource<Calibration>("/analytics/calibration", calibParams, ["market_views"]);
  const shadow = useResource<Shadow>("/analytics/shadow", marketParams, ["shadow"], { enabled: !!f.market });
  const rl = useResource<RlReport>("/analytics/rl/report", rlParams, ["market_views"], { enabled: chosen });
  const rlExp = useResource<RlExperiments>("/analytics/rl/experiments", undefined, ["market_views"]);
  const fo = useResource<FoScorecard>("/analytics/fo/scorecard", undefined, ["fo"], { enabled: hasFo });

  // 계좌 목록이 실패하면 계좌에 묶인 절은 그 오류를 보인다(영원히 「불러오는 중」 이 되지 않게).
  const needsAccount = <T,>(res: Resource<T>, what: string, render: (d: T) => ReactNode) =>
    accounts.error ? failed("계좌", accounts.error) : accounts.data && !chosen ? <Empty title="계좌 없음" /> : body(res, what, render);

  return (
    <div className="screen" data-screen="performance">
      <h1 className="screen-title">성과</h1>

      <section id="performance-overview" className="screen-section">
        <h2>성과</h2>
        {accounts.data ? <FilterBar accounts={list} filters={f} onChange={(next) => setOver((o) => ({ ...o, ...next }))} /> : null}
        {needsAccount(perf, "성과", (d) => <OverviewBody perf={d} />)}
      </section>

      <section id="compare" className="screen-section">
        <h2>계좌 비교</h2>
        {body(compare, "계좌 비교", (d) => <CompareTable data={d} />)}
      </section>

      <section id="calibration" className="screen-section">
        <h2>보정</h2>
        {body(calib, "보정", (d) => <CalibrationTable data={d} />)}
      </section>

      <section id="shadow" className="screen-section">
        <h2>섀도 기준선</h2>
        {needsAccount(shadow, "섀도", (d) => <ShadowTables data={d} />)}
      </section>

      <section id="rl" className="screen-section">
        <h2>RL</h2>
        {needsAccount(rl, "RL 보고서", (d) => <RlSummary report={d} />)}
        <h3 className="perf-sub">실험</h3>
        {body(rlExp, "RL 실험", (d) => <RlExperimentsTable data={d} />)}
      </section>

      {hasFo ? (
        <section id="fo-scorecard" className="screen-section">
          <h2>파생 scorecard</h2>
          {body(fo, "파생 scorecard", (d) => (
            <div className="perf-stats">
              <Card title="결정">
                <p className="perf-stat-value">{d.decisions}</p>
              </Card>
              <Card title="만기">
                <p className="perf-stat-value">{d.matured}</p>
              </Card>
              <Card title="적중률">
                <p className="perf-stat-value">{fmtPct(d.hit_rate)}</p>
              </Card>
              <Card title="계약 손익">
                <p className="perf-stat-value">{fmtKrw(d.contract_pnl)}</p>
              </Card>
            </div>
          ))}
        </section>
      ) : null}

      <section id="memory" className="screen-section">
        <h2>복기</h2>
        <MemoryBlock />
      </section>
    </div>
  );
}
