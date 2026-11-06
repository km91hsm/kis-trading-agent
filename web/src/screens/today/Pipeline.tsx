// 「아침 파이프라인」 절 — 시장(KR·US)마다 카드: 단계 칩(스캔 → 시장 세션 → 분석 n종목·결정 m) · 발굴 줄 · 뉴스 발굴 요청 수 ·
// 종목 표(코드+이름 · 마지막 단계 · 단계 수). 그날 기록이 없는 시장은 「기록 없음」 한 줄.
import { Card } from "../../components/Card";
import { Chip } from "../../components/Chip";
import { Empty } from "../../components/Empty";
import { Table, type Column } from "../../components/Table";
import type { Resource } from "../../lib/hooks";
import { pipelineOf, type MarketToday, type PipelineRow } from "./morning.ts";

const COLUMNS: Column<PipelineRow>[] = [
  { key: "label", label: "종목", width: "50%", long: true },
  { key: "last", label: "단계", width: "30%" },
  { key: "count", label: "단계 수", width: "20%", align: "num", render: (r) => String(r.count) },
];

function MarketCard({ market, res }: { market: string; res: Resource<MarketToday> }) {
  const body = () => {
    if (res.error) return <Empty title="불러오지 못했습니다">{res.error.message}</Empty>;
    if (!res.data) return <Empty title="불러오는 중" />;
    const p = pipelineOf(res.data);
    if (!p) return <Empty title="기록 없음">이 날 이 시장의 아침 파이프라인은 돌지 않았습니다.</Empty>;
    return (
      <>
        <p className="pipeline-steps chips">
          {p.steps.map((s) => (
            <Chip key={s.id} tone={s.done ? "ok" : "muted"}>
              {s.label}
            </Chip>
          ))}
          {p.capHit ? <Chip tone="warn">분석 상한 도달</Chip> : null}
        </p>
        {p.picks ? <p className="pipeline-line">발굴 {p.picks}</p> : null}
        {p.newsJobs ? <p className="pipeline-line">뉴스 발굴 요청 {p.newsJobs}건</p> : null}
        <Table columns={COLUMNS} rows={p.rows} rowKey={(r) => r.ticker} empty="분석한 종목 없음" />
      </>
    );
  };
  return (
    <div data-market={market}>
      <Card title={market}>{body()}</Card>
    </div>
  );
}

export function Pipeline({ kr, us }: { kr: Resource<MarketToday>; us: Resource<MarketToday> }) {
  return (
    <div className="pipeline-grid">
      <MarketCard market="KR" res={kr} />
      <MarketCard market="US" res={us} />
    </div>
  );
}
