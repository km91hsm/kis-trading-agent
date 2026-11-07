import { Chip } from "../../components/Chip";
import { Stats } from "../../components/Stats";
import { Table, type Column } from "../../components/Table";
import { fmtPct, fmtPt, fmtWhen } from "../../lib/format";
import { useResource } from "../../lib/hooks";
import { body } from "../performance/ResourceView";
import "./macro-overview.css";

type MacroItem = {
  key: string; label: string; instrument: string; unit: string;
  value: string | null; status: "available" | "unavailable"; reason: string | null;
  source: string | null; series_id: string | null; source_metadata: string | null;
  proxy: boolean; proxy_reason: string | null;
  observed_date: string | null; observed_at: string | null; received_at: string | null;
  stored_at: string | null; stored_at_basis: string;
  change_pct: number | null; change_reason: string | null; live_verified: boolean;
  freshness: { status: string; observation_age_days: number | null; reason: string };
};
type MacroResponse = {
  market: "KR" | "US"; asof: string; items: MacroItem[];
  scan: { lookback_days: number; max_files: number; files_read: number; skipped: number; truncated: boolean };
};

const columns: Column<MacroItem>[] = [
  { key: "label", label: "지표", width: "18%", long: true, render: (r) => r.label },
  { key: "source", label: "출처·상품", width: "25%", render: (r) => <>
    <span>{r.source ?? "출처 없음"}{r.series_id ? ` / ${r.series_id}` : ""}</span>
    <p className="macro-detail-note">{r.instrument}</p>
    {r.source_metadata === "legacy_mapping" && <p className="macro-detail-note">과거 수집 형식의 원천 매핑</p>}
  </> },
  { key: "stored_at", label: "저장·수신 시각 (KST)", width: "22%", render: (r) => <>
    <span title={r.stored_at ?? undefined}>저장 {fmtWhen(r.stored_at)}</span>
    <p className="macro-detail-note" title={r.received_at ?? undefined}>수신 {fmtWhen(r.received_at)}</p>
    <p className="macro-detail-note">저장은 파일 수정 시각</p>
  </> },
  { key: "freshness", label: "자료 한계", width: "35%", render: (r) => <>
    {r.proxy_reason && <p className="macro-detail-note">{r.proxy_reason}</p>}
    <p className="macro-detail-note">{r.reason ?? r.freshness.reason}</p>
    {r.freshness.observation_age_days !== null && <p className="macro-detail-note">관측 기준일 {r.freshness.observation_age_days}일 경과</p>}
    {r.change_reason && <p className="macro-detail-note">{r.change_reason}</p>}
  </> },
];

function Change({ item }: { item: MacroItem }) {
  if (item.value === null || item.change_pct === null) return <span title={item.change_reason ?? undefined}>등락 미제공</span>;
  const tone = item.change_pct > 0 ? "up" : item.change_pct < 0 ? "down" : "flat";
  return <span className="macro-change" data-direction={tone}>
    {item.change_pct > 0 ? "+" : ""}{fmtPct(item.change_pct)}
  </span>;
}

/** 선택 시장의 입력에 저장된 관측값을 읽는다. 수집 시작 버튼은 제공하지 않는다. */
export function MacroOverview({ market }: { market: "KR" | "US" }) {
  const res = useResource<MacroResponse>("/macro", { market }, ["runs"]);
  // useResource는 갱신 동안 이전 응답을 보존한다. 시장 범위가 바뀌면 즉시 숨긴다.
  const scoped = res.data?.market === market ? res : { ...res, data: null };
  return <div className="macro-overview">
    <div className="macro-heading">
      <h3>주요 매크로</h3>
      <button className="btn" type="button" disabled={res.loading} onClick={res.reload}>저장값 다시 읽기</button>
    </div>
    {body(scoped, "매크로 관측값", (data) => <>
      <p className="macro-caption">{market} 분석 입력에 저장된 일별 관측값입니다.</p>
      <Stats size="kpi" items={data.items.map((item) => ({
        id: item.key,
        label: item.label,
        value: item.value === null ? "—" : <span className="macro-value">{fmtPt(item.value)} <small>{item.unit}</small></span>,
        sub: <div className="macro-stat-meta">
          {item.proxy && <Chip tone="warn">대체 지표</Chip>}
          <Change item={item} />
          <span title={item.observed_date ?? undefined}>{item.observed_date ? `기준 ${fmtWhen(item.observed_date)}` : "기준일 없음"}{item.source ? ` / ${item.source}` : ""}</span>
          {item.value === null && <span>{item.reason ?? "저장된 유효 관측값 없음"}</span>}
        </div>,
      }))} />
      <details className="macro-details">
        <summary>출처와 자료 한계</summary>
        <Table columns={columns} rows={data.items} rowKey={(r) => r.key} />
        <p className="macro-caption">최근 {data.scan.lookback_days}일의 저장 자료를 최대 {data.scan.max_files}개까지 확인합니다.</p>
        {data.scan.truncated && <p className="macro-limit">조회 한도에 도달해 일부 저장 자료가 제외됐습니다.</p>}
        {data.scan.skipped > 0 && <p className="macro-caption">손상·미래 시각 등으로 읽을 수 없는 저장 자료 {data.scan.skipped}개를 제외했습니다.</p>}
      </details>
    </>)}
  </div>;
}
