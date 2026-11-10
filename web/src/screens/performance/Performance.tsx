// 「성과」 화면 — 페이지 3개(nav.ts performance.pages): 성과 `/performance`(필터 계좌·시장·기간 → 개요 지표·NAV+벤치마크·판정 분포,
// 계좌×시장 비교) · 복기 `/performance/memory`(복기 메모리, 종목 검색 → q) · 고급 분석 `/performance/analysis`(판정 보정·기준선 비교·강화학습).
// 파생 성적표는 성과 페이지에서 파생 계좌를 선택했을 때 보인다. 절이 어느 페이지에 가는지는 nav.ts 항목의 `page` 가 정한다.
// 절 컨테이너는 데이터 전에 바로 그리고(절 id = nav.ts 항목), 절마다 로딩·오류를 따로 보인다.
import { useEffect, useMemo, type ReactNode } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
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
import { compareAllEmpty, filtersFromSearch, filtersSearch, type Calibration, type Compare, type FilterAccount, type Filters, type Perf, type Shadow } from "./performance.ts";
import { body, failed } from "./ResourceView";
import "./performance-details.css";
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
  const [params] = useSearchParams();
  const { hash } = useLocation();
  const navigate = useNavigate();
  const f = filtersFromSearch(list, params);
  const chosen = !!f.account_id && !!f.market;
  const hasFo = list.find((a) => a.id === f.account_id)?.lane === "fo";
  const normalizedSearch = filtersSearch(params, f).toString();
  // 로딩 중의 빈 목록으로 직접 접속한 계좌를 덮어쓰지 않는다.
  useEffect(() => {
    if (!accounts.data || accounts.loading || normalizedSearch === params.toString()) return;
    void navigate({ search: `?${normalizedSearch}`, hash }, { replace: true });
  }, [accounts.data, accounts.loading, normalizedSearch, params, navigate, hash]);
  const changeFilters = (next: Partial<Filters>) => {
    const search = filtersSearch(params, { ...f, ...next });
    void navigate({ search: `?${search}`, hash });
  };

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
    accounts.error ? failed("계좌", accounts.error, accounts.reload, accounts.loading) : accounts.data && !chosen ? <Empty title="계좌 없음" /> : body(res, what, render);

  const sections: Record<SectionId, { title: string; body: ReactNode; hidden?: boolean; description?: string }> = {
    "performance-overview": {
      title: "수익률·위험",
      body: (
        <>
          {accounts.data ? <FilterBar accounts={list} filters={f} onChange={changeFilters} /> : null}
          {needsAccount(perf, "성과", (d) => <OverviewBody perf={d} />)}
        </>
      ),
    },
    compare: { title: "계좌 비교", body: body(compare, "계좌 비교", (d) => (compareAllEmpty(d) ? <Empty title={`${d.days}일 기록 없음`} /> : <CompareTable data={d} />)) },
    memory: { title: "복기", body: <MemoryBlock /> },
    calibration: { title: "판정 보정", description: "전체 계좌의 분석 역할별 예측이 실제 결과와 얼마나 맞았는지 확인합니다.", body: body(calib, "보정", (d) => <CalibrationTable data={d} />) },
    shadow: { title: "기준선 비교", description: "선택 시장의 AI 판단 성과를 균등 투자·지수 보유 같은 단순 전략과 비교합니다.", body: needsAccount(shadow, "섀도", (d) => <ShadowTables data={d} />) },
    rl: {
      title: "강화학습 분석",
      description: "강화학습 모델의 투자 비중 제안과 가상 거래·실험 결과를 확인합니다.",
      body: (
        <>
          {needsAccount(rl, "RL 보고서", (d) => <RlSummary report={d} />)}
          <h3 className="perf-sub">가상 거래 · {f.market} (최근 30일)</h3>
          {needsAccount(rlShadow, "섀도 거래", (d) => <RlShadowTable data={{ ...d, rows: d.rows.filter((row) => row.market === f.market) }} />)}
          <h3 className="perf-sub">전체 학습 실험</h3>
          {body(rlExp, "RL 실험", (d) => <RlExperimentsTable data={d} />)}
        </>
      ),
    },
    "fo-scorecard": {
      title: "파생 성적표",
      hidden: !hasFo,
      body: hasFo
        ? body(fo, "파생 성적표", (d) => (
            <>
              <p className="perf-note">전체 파생 결정의 누적 집계입니다. 선택 계좌·시장·기간 필터와 별도로 계산합니다.</p>
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
            </>
          ))
        : null,
    },
  };

  return (
    <div className="screen" data-screen="performance" data-page={page}>
      <h1 className="screen-title">{page === "performance" ? "성과" : `성과 · ${pageLabel}`}</h1>
      <div className="screen-intro">
        <p className="screen-description">{page === "memory" ? "과거 판단의 복기 메모를 종목별로 찾아 다음 분석에 참고합니다." : page === "analysis" ? "판정 보정·기준선 비교·강화학습 결과를 확인합니다. 분석별 설명에서 계좌·시장·기간의 적용 범위를 확인하세요." : "계좌·시장·기간을 고른 뒤 수익과 위험을 함께 확인합니다. 비교 표도 계좌와 시장을 구분해 보여 줍니다."}</p>
        <div className="screen-actions">
          <Link to="/accounts">보유·주문 확인</Link>
          <Link to="/system/features#regime">현재 매수 정책 확인</Link>
        </div>
      </div>
      <PageTabs screen="performance" label="성과 페이지" search={accounts.data ? `?${normalizedSearch}` : `?${params}`} />
      {/* 섀도·RL 은 계좌·시장에 묶이는데 개요 절(필터 줄)이 다른 페이지에 있으면 고를 수 없다 → 그 페이지 머리에 같은 필터(기간 없이) */}
      {!on("performance-overview") && (on("shadow") || on("rl")) && accounts.data ? (
        <FilterBar accounts={list} filters={f} onChange={changeFilters} periods={false} />
      ) : null}
      {ids.map((id) => (
        <section key={id} id={id} className="screen-section" hidden={sections[id].hidden || undefined}>
          {sections[id].description ? (
            <details className="performance-details">
              <summary><span>{sections[id].title}</span><small>{sections[id].description}</small></summary>
              <div className="performance-details-body">{sections[id].body}</div>
            </details>
          ) : <><h2>{sections[id].title}</h2>{sections[id].body}</>}
        </section>
      ))}
    </div>
  );
}
