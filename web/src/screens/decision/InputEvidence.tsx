import { Chip } from "../../components/Chip";
import { Empty } from "../../components/Empty";
import { Stats } from "../../components/Stats";
import { Table, type Column } from "../../components/Table";
import { fmtWhen } from "../../lib/format";
import "./evidence.css";

export type InputItem = {
  id: string; group: string; refs: string[]; title: string; source: string | null;
  url: string | null; received_at: string | null; source_asof: string | null;
  cited: boolean; excerpt_truncated: boolean;
};
type SourceOutcome = { source: string; status: string; count: null };
type InputSection = { id: string; count: number; status: string; source: string | null; received_at: string | null; source_outcomes?: SourceOutcome[] };
export type InputGroup = { id: string; stored_count: number; reconstructed_count: number | null; omitted_count: number | null; status: string; received_at: string | null; listed_count: number; refs?: string[]; cited?: boolean; sections?: InputSection[] };
type InputReceipt = {
  stage: string; provider: string | null; recorded_at: string; context_hash: string;
  truncated: boolean; groups: { id: string; count: number; refs: string[]; identifiers_omitted: number; source_counts?: Record<string, number>; sections?: InputSection[] }[];
};
type SourceCoverage = { id: string; status: string; source: string | null; source_asof: string | null; received_at: string | null; items: number | null; gaps_count: number | null; stale: boolean | null };
export type DecisionInputs = {
  decision_id: number; snapshot: { status: string; asof: string | null };
  delivery: { status: string; receipts: InputReceipt[]; unreadable_count: number; unlisted_count: number };
  reconstruction: { status: string }; groups: InputGroup[]; items: InputItem[];
  source_coverage?: SourceCoverage[];
};
const LABELS: Record<string, string> = { technical: "시세·차트", fundamental: "재무·기업", sentiment: "투자자 수급·기타 입력", news: "뉴스", macro: "거시지표", events: "공시·일정", blog_posts: "블로그", community: "커뮤니티", saveticker: "SaveTicker", market_activity: "시장 거래대금" };
const label = (key: string) => LABELS[key] ?? key;
const snapshotLabel: Record<string, string> = { missing: "저장 입력 없음", unreadable: "저장 입력 읽기 실패", too_large: "저장 입력 크기 초과", present: "세션 당시 저장 입력" };
const groupStatus: Record<string, string> = { present: "저장됨", empty: "0건", error: "자료 없음·오류 기록", partial: "일부 자료·오류 기록", skipped: "의도적 생략" };
const coverageStatus: Record<string, string> = { present: "자료 있음", missing: "누락", partial: "부분 자료", dropped: "생략됨", unavailable: "사용 불가", truncated: "축약됨", unrecorded: "미기록" };
const coverageColumns: Column<SourceCoverage>[] = [
  { key: "id", label: "원천", width: "26%", long: true },
  { key: "status", label: "당시 상태", width: "18%", render: row => `${coverageStatus[row.status] ?? "미기록"}${row.stale ? " / 지연" : ""}` },
  { key: "items", label: "건수", width: "12%", render: row => row.items === null ? "미기록" : `${row.items}건` },
  { key: "source", label: "출처", width: "18%", render: row => row.source ?? "미기록" },
  { key: "source_asof", label: "관측 시각", width: "13%", render: row => fmtWhen(row.source_asof) },
  { key: "received_at", label: "수집 시각", width: "13%", render: row => fmtWhen(row.received_at) },
];

export function safeOriginalUrl(value: string | null): string | null {
  if (!value || value.length > 2048 || /\s/.test(value)) return null;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && url.hostname && !url.username && !url.password ? value : null;
  } catch { return null; }
}

const itemAnchor = (item: InputItem) => `input-${encodeURIComponent(item.id)}`;

export function EvidenceLinks({ refs, items, groups = [] }: { refs: string[]; items: InputItem[]; groups?: InputGroup[] }) {
  if (!refs.length) return <span className="data-muted">근거 미기록</span>;
  return <ul className="evidence-refs">{refs.map((ref, index) => {
    const item = items.find(row => row.refs.includes(ref));
    const matched = groups.filter(group => group.refs?.includes(ref));
    const anchor = item ? itemAnchor(item) : matched.length === 1 ? `input-group-${matched[0].id}` : matched.length ? "inputs" : null;
    return <li key={`${ref}-${index}`}>{anchor ? <a href={`#${anchor}`} onClick={() => {
      const target = document.getElementById(anchor);
      target?.closest("details")?.setAttribute("open", "");
      target?.focus();
    }}>{ref} — {item?.title ?? `${matched.map(group => label(group.id)).join("·")} 입력 묶음`}</a> : <span>{ref} <span className="data-muted">(입력 연결 미기록)</span></span>}</li>;
  })}</ul>;
}

const columns: Column<InputGroup>[] = [
  { key: "id", label: "입력", width: "23%", long: true, render: row => <span id={`input-group-${row.id}`} tabIndex={-1}>{label(row.id)}{row.cited ? " (묶음 인용)" : ""}</span> },
  { key: "stored_count", label: "당시 저장", width: "13%", align: "num", render: row => `${row.stored_count}건` },
  { key: "reconstructed_count", label: "현재 규칙 재구성", width: "20%", align: "num", render: row => row.reconstructed_count === null ? "대상 아님 / 미기록" : `${row.reconstructed_count}건` },
  { key: "omitted_count", label: "재구성 시 생략", width: "17%", align: "num", render: row => row.omitted_count === null ? "미기록" : `${row.omitted_count}건` },
  { key: "status", label: "수집 결과", width: "14%", render: row => <Chip tone={["error", "partial"].includes(row.status) ? "warn" : "muted"}>{groupStatus[row.status] ?? "미기록"}</Chip> },
  { key: "received_at", label: "수집 시각", width: "13%", render: row => fmtWhen(row.received_at) },
];

const outcomeStatus: Record<string, string> = { success: "성공", error: "실패", skipped: "의도적 생략", unrecorded: "미기록" };
function SourceOutcomes({ outcomes }: { outcomes?: SourceOutcome[] }) {
  if (!outcomes?.length) return <span className="data-muted">미기록</span>;
  return <><ul className="evidence-refs">{outcomes.map((outcome, i) => <li key={`${outcome.source}-${i}`}>{outcome.source === "unrecorded" ? "원천 미기록" : outcome.source}: {outcomeStatus[outcome.status] ?? "미기록"}</li>)}</ul><span className="data-muted">원천별 건수 미기록</span></>;
}
const sectionColumns: Column<InputSection>[] = [
  { key: "id", label: "입력 원천", width: "20%", long: true },
  { key: "status", label: "수집 결과", width: "20%", render: row => groupStatus[row.status] ?? "미기록" },
  { key: "count", label: "저장 자료", width: "10%", render: row => `${row.count}건` },
  { key: "source", label: "출처", width: "15%", render: row => row.source ?? "미기록" },
  { key: "received_at", label: "수집 시각", width: "15%", render: row => fmtWhen(row.received_at) },
  { key: "source_outcomes", label: "세부 원천 결과", width: "20%", render: row => <SourceOutcomes outcomes={row.source_outcomes} /> },
];

function Receipt({ receipt, items }: { receipt: InputReceipt; items: InputItem[] }) {
  return <li className="input-receipt">
    <strong>{receipt.stage}</strong>
    <Stats cols={3} items={[
      { label: "생성 시각", value: fmtWhen(receipt.recorded_at) },
      { label: "공급자", value: receipt.provider === "codex" ? "Codex" : receipt.provider === "claude" ? "Claude" : "미기록" },
      { label: "입력 축약", value: receipt.truncated ? "축약됨" : "축약 표시 없음" },
    ]} />
    <p>{receipt.groups.map(group => `${label(group.id)} ${group.count}건${group.identifiers_omitted ? ` (식별자 ${group.identifiers_omitted}건 생략)` : ""}`).join(", ") || "공개 입력 항목 없음"}</p>
    {receipt.groups.some(group => Object.keys(group.source_counts ?? {}).length) ? <ul>{receipt.groups.map(group => <li key={group.id}>{label(group.id)} 출처: {Object.entries(group.source_counts ?? {}).map(([source, count]) => `${source === "unrecorded" ? "출처 미기록" : source} ${count}건`).join(", ") || "미기록"}{group.identifiers_omitted ? " (표시된 식별자 범위)" : ""}</li>)}</ul> : null}
    {receipt.groups.some(group => group.sections?.some(section => ["error", "partial"].includes(section.status))) ? <p className="data-muted">오류 기록: {receipt.groups.flatMap(group => group.sections ?? []).filter(section => ["error", "partial"].includes(section.status)).map(section => `${section.id} ${groupStatus[section.status]} (자료 ${section.count}건)`).join(", ")}</p> : null}
    {receipt.groups.flatMap(group => group.sections ?? []).filter(section => section.source_outcomes?.length).map(section => <div key={section.id}><strong>{section.id} 원천 응답</strong><SourceOutcomes outcomes={section.source_outcomes} /></div>)}
    <EvidenceLinks refs={[...new Set(receipt.groups.flatMap(group => group.refs))].slice(0, 30)} items={items} />
    {receipt.groups.reduce((count, group) => count + group.refs.length, 0) > 30 ? <p className="data-muted">입력 연결은 앞의 30개 식별자까지 표시합니다.</p> : null}
    <details><summary>컨텍스트 해시</summary><code className="evidence-hash">{receipt.context_hash}</code></details>
  </li>;
}

export function InputEvidence({ data }: { data: DecisionInputs }) {
  return <>
    <Stats cols={3} items={[
      { label: "저장 상태", value: snapshotLabel[data.snapshot.status] ?? "저장 상태 미기록", num: false },
      { label: "세션 입력 기준 시각", value: fmtWhen(data.snapshot.asof), sub: data.snapshot.asof ?? "시각 미기록" },
      { label: "전달용 응답", value: data.delivery.status === "recorded" ? `${data.delivery.receipts.length}개 기록` : "전달용 응답 미기록", num: false },
    ]} />
    <p className="data-muted evidence-note">전달용 도구 응답·호출 준비 기록은 모델 수신이나 활용을 보장하지 않습니다. 보고서·토론에서 출처를 인용했는지 별도로 표시합니다.</p>
    {data.delivery.receipts.length ? <details className="input-receipts"><summary>전달용 도구 응답·호출 준비 기록 {data.delivery.receipts.length}개</summary><ul>{data.delivery.receipts.map((receipt, i) => <Receipt key={`${receipt.context_hash}-${i}`} receipt={receipt} items={data.items} />)}</ul></details> : null}
    {data.delivery.unreadable_count || data.delivery.unlisted_count ? <p className="data-muted">읽지 못한 전달 기록 {data.delivery.unreadable_count}개, 표시 한도를 넘은 기록 {data.delivery.unlisted_count}개</p> : null}
    {data.groups.length ? <>
      <p className="data-muted evidence-note">현재 규칙 재구성은 저장 입력에 현재 공개 컨텍스트 축약 규칙을 적용한 참고값이며 당시 전달을 증명하지 않습니다. 최종 응답에서는 추가로 축약될 수 있습니다.</p>
      <Table columns={columns} rows={data.groups} rowKey={row => row.id} />
      <details className="input-items"><summary>저장 항목과 원문 {data.items.length}개</summary>
        <ul>{data.items.map(item => {
          const url = safeOriginalUrl(item.url);
          return <li id={itemAnchor(item)} key={item.id} tabIndex={-1} className="input-item">
            <div className="input-item-head"><strong>{item.title}</strong><Chip tone="muted">{item.cited ? "보고서·토론에서 인용" : "인용 미기록"}</Chip></div>
            <Stats cols={3} items={[
              { label: "출처", value: item.source ?? "미기록", num: false },
              { label: "원문 기준 시각", value: fmtWhen(item.source_asof) },
              { label: "수집 시각", value: fmtWhen(item.received_at) },
            ]} />
            <p className="data-muted">근거 ID: {item.refs.filter(ref => !safeOriginalUrl(ref)).join(", ") || "미기록"}{item.excerpt_truncated ? " (발췌 축약됨)" : ""}</p>
            {url ? <a href={url} target="_blank" rel="noopener noreferrer">원문 열기<span className="sr-only">: {item.title} (새 탭)</span></a> : <span className="data-muted">안전한 원문 링크 없음</span>}
          </li>;
        })}</ul>
      </details>
      {data.groups.some(group => group.listed_count < group.stored_count) ? <p className="data-muted">항목별 상세는 원천마다 최대 500건을 표시합니다. 저장 건수에는 생략된 상세도 포함됩니다.</p> : null}
    </> : <Empty title={data.snapshot.status === "present" ? "저장 입력 항목 없음" : snapshotLabel[data.snapshot.status] ?? "저장 입력 항목 없음"} />}
    {data.groups.some(group => group.sections?.length) ? <details className="input-items"><summary>원천별 수집 결과</summary><Table columns={sectionColumns} rows={data.groups.flatMap(group => group.sections ?? [])} rowKey={row => row.id} /></details> : null}
    {data.source_coverage?.length ? <details className="input-items"><summary>원천별 누락·지연 기록</summary><Table columns={coverageColumns} rows={data.source_coverage} rowKey={row => row.id} /></details> : null}
    <p className="data-muted evidence-note">기록 범위: 전체 입력 읽기, 분석 절에 함께 제공되는 보조 공개 컨텍스트, 연구자·트레이더의 공개 컨텍스트, Codex 호출 준비. 분석 절 본문·별도 파일 열람·trader_context 전용 읽기의 전달과 외부 모델의 실제 수신은 확인하지 않습니다.</p>
  </>;
}
