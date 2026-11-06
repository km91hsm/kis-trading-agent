// 섀도 기준선 비교(평균차·95% 구간, min_pairs 메모)·최근 10일 표, RL 모델 요약·섀도 집계·섀도 거래 표(30행 Pager),
// 실험 표(행을 펼치면 GET /analytics/rl/experiments/{version} — 학습 기간·스텝·시드·폴드 Stats + 정책별 요약 표).
import { Card } from "../../components/Card";
import { Chip } from "../../components/Chip";
import { Empty } from "../../components/Empty";
import { Pager } from "../../components/Pager";
import { Stats } from "../../components/Stats";
import { Table, type Column } from "../../components/Table";
import { fmtDate, fmtInt, fmtPct, fmtWhen } from "../../lib/format";
import { useResource } from "../../lib/hooks";
import { tickerLabel } from "../../lib/ticker";
import { usePaged } from "../../lib/usePaged";
import { body } from "./ResourceView";
import { SHADOW_KEYS, shadowLabel, shadowRows, shadowSummary, type Shadow, type ShadowRow } from "./performance.ts";

export function ShadowTables({ data }: { data: Shadow }) {
  const pairCols: Column<ShadowRow>[] = [
    { key: "label", label: "기준선", width: "28%", long: true, render: (r) => r.label },
    { key: "n", label: "n", width: "10%", align: "num", render: (r) => String(r.n) },
    { key: "mean", label: "평균차", width: "16%", align: "num", render: (r) => r.mean },
    { key: "ci", label: "95% 구간", width: "24%", align: "num", render: (r) => r.ci },
    { key: "note", label: "비고", width: "22%", render: (r) => r.note || "—" },
  ];
  type Recent = Shadow["recent"][number];
  const recentCols: Column<Recent>[] = [
    { key: "date", label: "날짜", width: "20%", long: true, render: (r) => fmtDate(r.date) },
    { key: "llm", label: "LLM", width: "16%", align: "num", render: (r) => fmtPct(r.llm ?? null) },
    ...SHADOW_KEYS.map((k) => ({ key: k, label: shadowLabel(k), width: "16%", align: "num" as const, render: (r: Recent) => fmtPct(r[k] ?? null) })),
  ];
  // 기준선이 전부 표본 부족이면 같은 메모가 네 줄 반복되는 표 대신 한 줄.
  const summary = shadowSummary(data);
  return (
    <>
      <p className="perf-note">LLM − 기준선 선행 수익률 차 · 최소 쌍 {data.min_pairs}</p>
      {summary ? <Empty title={summary} /> : <Table columns={pairCols} rows={shadowRows(data)} rowKey={(r) => r.key} empty="만기 쌍 없음" />}
      <h3 className="perf-sub">최근 10일</h3>
      <Table columns={recentCols} rows={data.recent ?? []} rowKey={(r) => r.date} empty="최근 기록 없음" />
    </>
  );
}

export type RlModel = { version: string; saved_at: string | null; backend: string; steps: number | null; promoted: boolean; promote_reason: string | null };
export type RlReport = {
  model: RlModel | null;
  shadow: { n: number; applied: number; avg_llm_weight: number | null; avg_proposed_weight: number | null; matured: number; realized_rl_sum: number | null; realized_llm_sum: number | null };
};
export type RlExperiments = { experiments: RlModel[]; active: RlModel | null };
type PolicySummary = { n: number; return_mean: number | null; return_std: number | null; mdd_mean: number | null; sortino_mean: number | null };
type RlExperimentDetail = RlModel & {
  market: string | null;
  seeds: number | number[] | null;
  horizon_days: number | null;
  train_range: unknown;
  folds: unknown[] | null;
  summary: Record<string, PolicySummary> | null;
};
export type RlShadowRow = {
  id: number;
  decision_id: number | null;
  account_id: string;
  created_at: string;
  model_version: string | null;
  ticker: string | null;
  market: string | null;
  name: string | null;
  decision_date: string | null;
  llm_weight: number | null;
  proposed_weight: number | null;
  applied: boolean;
  price_return: number | null;
  realized_rl: number | null;
  realized_llm: number | null;
};
export type RlShadow = { rows: RlShadowRow[] };
const POLICY_LABEL: Record<string, string> = { rl: "RL", fixed: "고정 비중", llm: "LLM", vol_target: "변동성 목표" };
const NO_PARAMS = {};

export function RlSummary({ report }: { report: RlReport }) {
  const m = report.model;
  const s = report.shadow;
  return (
    <div className="perf-cards">
      <Card title="모델" right={m ? <Chip tone={m.promoted ? "ok" : "muted"}>{m.promoted ? "승격" : "미승격"}</Chip> : null}>
        {m ? (
          <Stats
            cols={4}
            items={[
              { label: "버전", value: m.version, num: false },
              { label: "백엔드", value: m.backend, num: false },
              { label: "스텝", value: fmtInt(m.steps) },
              { label: "저장일", value: fmtDate(m.saved_at) },
            ]}
          />
        ) : (
          <Empty title="활성 모델 없음" />
        )}
      </Card>
      <Card title="섀도 집계">
        <Stats
          cols={3}
          items={[
            { label: "제안", value: String(s.n) },
            { label: "적용", value: String(s.applied) },
            { label: "만기", value: String(s.matured) },
            { label: "평균 비중 LLM / RL", value: `${fmtPct(s.avg_llm_weight)} / ${fmtPct(s.avg_proposed_weight)}` },
            { label: "실현 합 RL / LLM", value: `${fmtPct(s.realized_rl_sum)} / ${fmtPct(s.realized_llm_sum)}` },
          ]}
        />
      </Card>
    </div>
  );
}

export function RlExperimentsTable({ data }: { data: RlExperiments }) {
  const columns: Column<RlModel>[] = [
    { key: "version", label: "버전", width: "20%", long: true, render: (r) => r.version },
    { key: "saved_at", label: "저장", width: "16%", render: (r) => fmtDate(r.saved_at) },
    { key: "backend", label: "백엔드", width: "14%", render: (r) => r.backend },
    { key: "steps", label: "스텝", width: "14%", align: "num", render: (r) => String(r.steps ?? "—") },
    { key: "promoted", label: "승격", width: "12%", align: "chip", render: (r) => <Chip tone={r.promoted ? "ok" : "muted"}>{r.promoted ? "승격" : "미승격"}</Chip> },
    { key: "reason", label: "사유", width: "24%", render: (r) => r.promote_reason ?? "—" },
  ];
  return <Table columns={columns} rows={data.experiments} rowKey={(r) => r.version} empty="실험 없음" expand={(r) => <RlExperimentBlock version={r.version} />} />;
}

const range = (r: unknown): string => (Array.isArray(r) && r.length === 2 ? `${r[0]} ~ ${r[1]}` : "—");
const seedCount = (s: number | number[] | null): string => (Array.isArray(s) ? String(s.length) : fmtInt(s));

function RlExperimentBlock({ version }: { version: string }) {
  const res = useResource<RlExperimentDetail>(`/analytics/rl/experiments/${version}`, NO_PARAMS, ["market_views"]);
  type Row = { policy: string } & PolicySummary;
  const columns: Column<Row>[] = [
    { key: "policy", label: "정책", width: "20%", render: (r) => POLICY_LABEL[r.policy] ?? r.policy },
    { key: "n", label: "표본 수(n)", width: "12%", align: "num", render: (r) => String(r.n) },
    { key: "return_mean", label: "수익 평균", width: "17%", align: "num", render: (r) => fmtPct(r.return_mean) },
    { key: "return_std", label: "수익 표준편차", width: "17%", align: "num", render: (r) => fmtPct(r.return_std) },
    { key: "mdd_mean", label: "최대 낙폭(MDD) 평균", width: "17%", align: "num", render: (r) => fmtPct(r.mdd_mean) },
    { key: "sortino_mean", label: "소르티노 평균", width: "17%", align: "num", render: (r) => (r.sortino_mean === null ? "—" : r.sortino_mean.toFixed(2)) },
  ];
  return body(res, "실험 상세", (d) => (
    <>
      <Stats
        cols={4}
        items={[
          { label: "학습 기간", value: range(d.train_range), num: false },
          { label: "스텝", value: fmtInt(d.steps) },
          { label: "시드", value: seedCount(d.seeds) },
          { label: "폴드", value: fmtInt(d.folds?.length ?? null) },
          { label: "지평(일)", value: fmtInt(d.horizon_days) },
          { label: "시장", value: d.market ?? "전체", num: false },
        ]}
      />
      <Table columns={columns} rows={Object.entries(d.summary ?? {}).map(([policy, s]) => ({ policy, ...s }))} rowKey={(r) => r.policy} empty="요약 없음" />
    </>
  ));
}

export function RlShadowTable({ data }: { data: RlShadow }) {
  const paged = usePaged(data.rows, 30, "spage");
  const columns: Column<RlShadowRow>[] = [
    { key: "created_at", label: "시각", width: "14%", render: (r) => fmtWhen(r.created_at) },
    { key: "ticker", label: "종목", width: "16%", long: true, render: (r) => (r.ticker ? `${tickerLabel(r.ticker, r.name)}${r.market ? ` · ${r.market}` : ""}` : "—") },
    { key: "model", label: "모델", width: "14%", render: (r) => r.model_version ?? "—" },
    { key: "weights", label: "비중 LLM / RL", width: "16%", align: "num", render: (r) => `${fmtPct(r.llm_weight)} / ${fmtPct(r.proposed_weight)}` },
    { key: "applied", label: "적용", width: "10%", align: "chip", render: (r) => <Chip tone={r.applied ? "ok" : "muted"}>{r.applied ? "적용" : "제안만"}</Chip> },
    { key: "price_return", label: "수익률", width: "12%", align: "num", render: (r) => fmtPct(r.price_return) },
    { key: "realized", label: "실현 RL / LLM", width: "18%", align: "num", render: (r) => `${fmtPct(r.realized_rl)} / ${fmtPct(r.realized_llm)}` },
  ];
  return (
    <>
      <Table columns={columns} rows={paged.rows} rowKey={(r) => r.id} empty="섀도 거래 없음" />
      <Pager {...paged} />
    </>
  );
}
