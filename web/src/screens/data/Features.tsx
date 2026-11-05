// 지표·모델 — GET /features/latest?market= 를 KEY_COLUMNS 표로(머리글 누르면 정렬, aria-sort), 행을 누르면
// GET /features/history?days=120 종가 추이·최근 행. 모델은 GET /ml/models(활성 칩·note). 잡 버튼은 비매매(trading=false).
import { useMemo, useState } from "react";
import { Chip } from "../../components/Chip";
import { Empty } from "../../components/Empty";
import { RunJobButton } from "../../components/RunJobButton";
import { Sparkline } from "../../components/Sparkline";
import { Table, type Column } from "../../components/Table";
import { fmtPct } from "../../lib/format";
import { useResource } from "../../lib/hooks";
import { body } from "../performance/ResourceView";
import { MarketSelect, type Market } from "./Data.tsx";
import { featureRows, fmt, KEY_COLUMNS, type FeatureRow, type HistoryResp, type LatestResp, type SortDir } from "./data.ts";

type Model = {
  name: string;
  version: string;
  saved_at: string | null;
  promoted: boolean;
  promote_reason: string | null;
  promoted_at: string | null;
  market: string | null;
  rows: number | null;
  train_range: unknown;
  backend: string | null;
  metrics: { accuracy: number | null; baseline: number | null; alpha: number | null; n_test: number | null } | null;
};
type ModelsResp = { models: Model[]; active: Model | null; note: string | null };

const HISTORY_COLUMNS = ["date", "close", "roc_10", "high_52w_pos", "flow_foreign_20d", "news_count"];
const NO_PARAMS = {};

export function Features() {
  const [market, setMarket] = useState<Market>("KR");
  const [sort, setSort] = useState<{ key: string | null; dir: SortDir }>({ key: null, dir: "desc" });
  const [picked, setPicked] = useState<string | null>(null);
  const params = useMemo(() => ({ market }), [market]);
  const jobParams = useMemo(() => ({ market }), [market]);
  const latest = useResource<LatestResp>("/features/latest", params, ["features"]);
  const models = useResource<ModelsResp>("/ml/models", undefined, ["features"]);
  const rows = useMemo(() => (latest.data ? featureRows(latest.data, sort.key, sort.dir) : []), [latest.data, sort]);

  const onSort = (key: string) => setSort((s) => (s.key === key ? { key, dir: s.dir === "desc" ? "asc" : "desc" } : { key, dir: "desc" }));
  const ariaSort = (key: string) => (sort.key !== key ? "none" : sort.dir === "asc" ? "ascending" : "descending");

  return (
    <>
      <div className="perf-filters">
        <MarketSelect
          value={market}
          onChange={(m) => {
            setMarket(m);
            setPicked(null);
          }}
        />
        {latest.data?.asof ? <span className="data-muted">기준 {latest.data.asof}</span> : null}
      </div>
      {body(latest, "지표", () =>
        rows.length ? (
          <div className="table-scroll">
            <table className="table data-feature-table">
              <thead>
                <tr>
                  {KEY_COLUMNS.map((c) => (
                    <th key={c} data-key={c} aria-sort={ariaSort(c)} scope="col">
                      <button type="button" className="data-sort" onClick={() => onSort(c)}>
                        {c}
                      </button>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const t = String(r.ticker);
                  return (
                    <tr
                      key={t}
                      tabIndex={0}
                      className="data-feature-row"
                      aria-selected={picked === t}
                      onClick={() => setPicked(t)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") setPicked(t);
                      }}
                    >
                      {KEY_COLUMNS.map((c) => (
                        <td key={c} className={typeof r[c] === "number" ? "cell-num" : "cell-text"}>
                          {fmt(r[c])}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title="지표 없음" />
        ),
      )}
      {picked ? <History market={market} ticker={picked} /> : null}

      <h3 className="perf-sub">모델</h3>
      {body(models, "모델", (d) => <Models data={d} />)}
      <div className="data-jobs">
        <RunJobButton kind="features_snapshot" params={jobParams} label="지표 스냅샷" trading={false} realAccounts={[]} />
        <RunJobButton kind="features_derive" params={NO_PARAMS} label="파생 지표 계산" trading={false} realAccounts={[]} />
        <RunJobButton kind="features_backfill" params={jobParams} label="지표 백필" trading={false} realAccounts={[]} />
      </div>
    </>
  );
}

function History({ market, ticker }: { market: Market; ticker: string }) {
  const params = useMemo(() => ({ market, ticker, days: "120" }), [market, ticker]);
  const res = useResource<HistoryResp>("/features/history", params, ["features"]);
  return (
    <div className="data-history">
      <h3 className="perf-sub">{ticker} 이력(120일)</h3>
      {body(res, "지표 이력", (d) => {
        if (!d.rows.length) return <Empty title="이력 없음" />;
        const closes = d.rows.map((r) => Number(r.close)).filter((v) => Number.isFinite(v));
        const recent = d.rows.slice(-5).reverse();
        const columns: Column<FeatureRow>[] = HISTORY_COLUMNS.map((c) => ({
          key: c,
          label: c,
          width: `${Math.floor(100 / HISTORY_COLUMNS.length)}%`,
          align: c === "date" ? "text" : "num",
          render: (r: FeatureRow) => fmt(r[c]),
        }));
        return (
          <>
            {closes.length ? (
              <p className="data-muted">
                종가 <Sparkline values={closes} width={160} height={32} />
              </p>
            ) : null}
            <Table columns={columns} rows={recent} rowKey={(r) => String(r.date)} />
          </>
        );
      })}
    </div>
  );
}

function Models({ data }: { data: ModelsResp }) {
  const columns: Column<Model>[] = [
    { key: "version", label: "버전", width: "22%", long: true, render: (m) => m.version },
    { key: "market", label: "시장", width: "10%", render: (m) => m.market ?? "—" },
    { key: "backend", label: "백엔드", width: "16%", render: (m) => m.backend ?? "—" },
    { key: "accuracy", label: "정확도", width: "12%", align: "num", render: (m) => fmtPct(m.metrics?.accuracy ?? null) },
    { key: "baseline", label: "기준선", width: "12%", align: "num", render: (m) => fmtPct(m.metrics?.baseline ?? null) },
    { key: "alpha", label: "알파", width: "12%", align: "num", render: (m) => fmtPct(m.metrics?.alpha ?? null) },
    { key: "promoted", label: "승격", width: "16%", align: "chip", render: (m) => <Chip tone={m.promoted ? "ok" : "muted"}>{m.promoted ? "승격" : "보류"}</Chip> },
  ];
  return (
    <>
      <p className="perf-note">
        {data.active ? <Chip tone="ok">활성 {data.active.version}</Chip> : <Chip>활성 모델 없음</Chip>} {data.note ? <span className="data-muted">{data.note}</span> : null}
      </p>
      <Table columns={columns} rows={data.models} rowKey={(m) => `${m.name}-${m.version}`} empty="모델 없음" />
    </>
  );
}
