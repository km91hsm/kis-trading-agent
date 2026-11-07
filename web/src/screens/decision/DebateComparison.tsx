import { Chip } from "../../components/Chip";
import { Empty } from "../../components/Empty";
import { Table, type Column } from "../../components/Table";
import type { TraceDebate } from "../../lib/trace";
import { EvidenceLinks, type InputItem, type InputGroup } from "./InputEvidence";

type Entry = { turn: TraceDebate; text: string; evidence: string[] };
type Issue = { id: string; topic: string; claims: Entry[]; responses: Entry[]; replies: Entry[] };
const sideLabel = (side: string | null) => side === "bull" ? "강세 측" : side === "bear" ? "약세 측" : "역할 미기록";
const participant = (turn: TraceDebate) => `${turn.provider === "claude" ? "Claude" : turn.provider === "codex" ? "Codex" : "공급자 미기록"} (${sideLabel(turn.side)})`;

function issueRows(turns: TraceDebate[]): Issue[] {
  const issues = new Map<string, Issue>();
  const get = (id: string) => {
    if (!issues.has(id)) issues.set(id, { id, topic: "원 주장 미기록", claims: [], responses: [], replies: [] });
    return issues.get(id)!;
  };
  const ordered = [...turns].sort((a, b) => (a.round ?? -1) - (b.round ?? -1));
  for (const turn of ordered) {
    for (const claim of turn.issues ?? []) {
      const issue = get(claim.issue_id);
      issue.topic = claim.topic;
      issue.claims.push({ turn, text: claim.claim, evidence: claim.evidence });
    }
    for (const response of turn.responses ?? []) get(response.issue_id).responses.push({ turn, text: response.response, evidence: response.evidence });
    for (const reply of turn.replies ?? []) get(reply.issue_id).replies.push({ turn, text: reply.response, evidence: reply.evidence });
  }
  return [...issues.values()];
}

type Confidence = { key: string; participant: string; change: string; rounds: string };
function confidenceRows(turns: TraceDebate[]): Confidence[] {
  const rows = new Map<string, TraceDebate[]>();
  for (const turn of [...turns].sort((a, b) => (a.round ?? -1) - (b.round ?? -1))) {
    const key = `${turn.side}:${turn.provider ?? "unknown"}:${turn.model ?? "unknown"}`;
    rows.set(key, [...(rows.get(key) ?? []), turn]);
  }
  return [...rows].map(([key, all]) => {
    const valid = all.filter(turn => typeof turn.conviction === "number" && Number.isFinite(turn.conviction) && turn.conviction >= 0 && turn.conviction <= 1);
    const first = valid[0], last = valid.at(-1);
    return {
      key, participant: participant(all[0]),
      change: first && last && valid.length > 1 ? `${Math.round(first.conviction! * 100)}% → ${Math.round(last.conviction! * 100)}%` : first ? `${Math.round(first.conviction! * 100)}% (변화 미기록)` : "확신 미기록",
      rounds: first && last ? `r${first.round ?? "?"} → r${last.round ?? "?"}` : "미기록",
    };
  });
}
const confidenceColumns: Column<Confidence>[] = [
  { key: "participant", label: "참여자", width: "42%", long: true },
  { key: "change", label: "발언 전체 확신 변화", width: "38%" },
  { key: "rounds", label: "기록 라운드", width: "20%" },
];

function Entries({ title, entries, items, groups }: { title: string; entries: Entry[]; items: InputItem[]; groups: InputGroup[] }) {
  return <div className="issue-column"><h4>{title}</h4>{entries.length ? <ul>{entries.map((entry, index) => <li key={index}>
    <p className="data-muted">r{entry.turn.round ?? "?"} {participant(entry.turn)}</p>
    <p>{entry.text}</p><EvidenceLinks refs={entry.evidence} items={items} groups={groups} />
  </li>)}</ul> : <p className="data-muted">미기록</p>}</div>;
}

export function DebateComparison({ turns, items, groups = [] }: { turns: TraceDebate[]; items: InputItem[]; groups?: InputGroup[] }) {
  const issues = issueRows(turns);
  return <>
    {turns.length ? <Table columns={confidenceColumns} rows={confidenceRows(turns)} rowKey={row => row.key} /> : null}
    <p className="data-muted evidence-note">확신은 발언 전체의 값입니다. 쟁점별 확신·명시적 수락/거부는 저장되지 않아 의견의 유사성만으로 합의를 판단하지 않습니다.</p>
    {issues.length ? <div className="issue-comparison">{issues.map(issue => <article key={issue.id} data-issue-id={issue.id}>
      <div className="issue-heading"><h3>{issue.topic}</h3><Chip tone="muted">합의 미기록</Chip></div>
      <p className="data-muted">쟁점 ID: {issue.id}</p>
      <div className="issue-columns">
        <Entries title="주장" entries={issue.claims} items={items} groups={groups} />
        <Entries title="상대 반론" entries={issue.responses} items={items} groups={groups} />
        <Entries title="재답변" entries={issue.replies} items={items} groups={groups} />
      </div>
    </article>)}</div> : <Empty title="구조화된 쟁점 기록 없음">이전 형식의 의견은 아래 토론 턴에서 확인할 수 있습니다.</Empty>}
  </>;
}
