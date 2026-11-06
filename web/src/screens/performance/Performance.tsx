// 「성과」 화면 — 페이지 3개(nav.ts performance.pages): 성과 `/performance`(필터 계좌·시장·기간 → 개요 지표·NAV+벤치마크·판정 분포,
// 계좌×시장 비교) · 복기 `/performance/memory`(복기 메모리, 종목 검색 → q) · 추가 분석 `/performance/analysis`(보정·섀도 기준선·RL·
// 파생 성적표 — fo 계좌가 없으면 hidden). 절이 어느 페이지에 가는지는 nav.ts 항목의 `page` 가 정한다.
// 절 컨테이너는 데이터 전에 바로 그리고(절 id = nav.ts 항목), 절마다 로딩·오류를 따로 보인다.
import { useMemo, useState, type ReactNode } from "react";
import { Empty } from "../../components/Empty";
import { PageTabs } from "../../components/PageTabs";
import { Stats } from "../../components/Stats";
import { NAV } from "../../generated/nav";
import { fmtKrw, fmtPct } from "../../lib/format";
import { useResource, type Resource } from "../../lib/hooks";
import { screenPages } from "../../lib/nav";
import { CalibrationTable } from "./CalibrationTable";
import { CompareTable } from "./CompareTable";
import { MemoryBlock } from "./MemoryBlock";
import { FilterBar, OverviewBody } from "./Overview";
import { compareAllEmpty, defaultFilters, type Calibration, type Compare, type FilterAccount, type Filters, type Perf, type Shadow } from "./performance.ts";
import { body, failed } from "./ResourceView";
import { RlExperimentsTable, RlShadowTable, RlSummary, ShadowTables, type RlExperiments, type RlReport, type RlShadow } from "./ShadowTables";

type FoScorecard = { decisions: number; matured: number; hit_rate: number | null; contract_pnl: string | number | null };

export type PerformancePage = (typeof NAV.performance.pages)[number]["id"];
type SectionId = (typeof NAV.performance.items)[number]["id"];

export function Performance({ page = "performance" }: { page?: PerformancePage }) {
  const pages = screenPages("performance");
  const pageLabel = pages.find((p) => p.id === page)?.label ?? "";
  const ids = NAV.performance.items.filter((i) => i.page === page).map((i) => i.id as SectionId);
  const on = (id: SectionId) => ids.includes(id);
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

  // 이 페이지에 없는 절의 자원은 부르지 않는다.
  const perf = useResource<Perf>("/analytics/performance", perfParams, ["market_views", "accounts", "orders"], { enabled: chosen && on("performance-overview") });
  const compare = useResource<Compare>("/analytics/performance/compare", daysParams, ["accounts", "orders"], { enabled: on("compare") });
  const calib = useResource<Calibration>("/analytics/calibration", calibParams, ["market_views"], { enabled: on("calibration") });
  const shadow = useResource<Shadow>("/analytics/shadow", marketParams, ["shadow"], { enabled: !!f.market && on("shadow") });
  const rl = useResource<RlReport>("/analytics/rl/report", rlParams, ["market_views"], { enabled: chosen && on("rl") });
  const rlExp = useResource<RlExperiments>("/analytics/rl/experiments", undefined, ["market_views"], { enabled: on("rl") });
  const rlShadowParams = useMemo(() => ({ account_id: f.account_id, days: "30" }), [f.account_id]);
  const rlShadow = useResource<RlShadow>("/analytics/rl/shadow", rlShadowParams, ["market_views"], { enabled: chosen && on("rl") });
  const fo = useResource<FoScorecard>("/analytics/fo/scorecard", undefined, ["fo"], { enabled: hasFo && on("fo-scorecard") });

  // 계좌 목록이 실패하면 계좌에 묶인 절은 그 오류를 보인다(영원히 「불러오는 중」 이 되지 않게).
  const needsAccount = <T,>(res: Resource<T>, what: string, render: (d: T) => ReactNode) =>
    accounts.error ? failed("계좌", accounts.error) : accounts.data && !chosen ? <Empty title="계좌 없음" /> : body(res, what, render);

  const sections: Record<SectionId, { title: string; body: ReactNode; hidden?: boolean }> = {
    "performance-overview": {
      title: "수익률·위험",
      body: (
        <>
          {accounts.data ? <FilterBar accounts={list} filters={f} onChange={(next) => setOver((o) => ({ ...o, ...next }))} /> : null}
          {needsAccount(perf, "성과", (d) => <OverviewBody perf={d} />)}
        </>
      ),
    },
    compare: { title: "계좌 비교", body: body(compare, "계좌 비교", (d) => (compareAllEmpty(d) ? <Empty title={`${d.days}일 기록 없음`} /> : <CompareTable data={d} />)) },
    memory: { title: "복기", body: <MemoryBlock /> },
    calibration: { title: "보정", body: body(calib, "보정", (d) => <CalibrationTable data={d} />) },
    shadow: { title: "섀도 기준선", body: needsAccount(shadow, "섀도", (d) => <ShadowTables data={d} />) },
    rl: {
      title: "RL",
      body: (
        <>
          {needsAccount(rl, "RL 보고서", (d) => <RlSummary report={d} />)}
          <h3 className="perf-sub">섀도 거래(30일)</h3>
          {needsAccount(rlShadow, "섀도 거래", (d) => <RlShadowTable data={d} />)}
          <h3 className="perf-sub">실험</h3>
          {body(rlExp, "RL 실험", (d) => <RlExperimentsTable data={d} />)}
        </>
      ),
    },
    "fo-scorecard": {
      title: "파생 성적표",
      hidden: !hasFo,
      body: hasFo
        ? body(fo, "파생 성적표", (d) => (
            <Stats
              size="kpi"
              cols={4}
              items={[
                { label: "결정", value: String(d.decisions) },
                { label: "만기", value: String(d.matured) },
                { label: "적중률", value: fmtPct(d.hit_rate) },
                { label: "계약 손익", value: fmtKrw(d.contract_pnl) },
              ]}
            />
          ))
        : null,
    },
  };

  return (
    <div className="screen" data-screen="performance" data-page={page}>
      <h1 className="screen-title">{page === "performance" ? "성과" : `성과 · ${pageLabel}`}</h1>
      <PageTabs screen="performance" label="성과 페이지" />
      {/* 섀도·RL 은 계좌·시장에 묶이는데 개요 절(필터 줄)이 다른 페이지에 있으면 고를 수 없다 → 그 페이지 머리에 같은 필터(기간 없이) */}
      {!on("performance-overview") && (on("shadow") || on("rl")) && accounts.data ? (
        <FilterBar accounts={list} filters={f} onChange={(next) => setOver((o) => ({ ...o, ...next }))} periods={false} />
      ) : null}
      {ids.map((id) => (
        <section key={id} id={id} className="screen-section" hidden={sections[id].hidden || undefined}>
          <h2>{sections[id].title}</h2>
          {sections[id].body}
        </section>
      ))}
    </div>
  );
}
