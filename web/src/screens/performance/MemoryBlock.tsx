// 복기 메모리 — 종목 검색(「검색」 → q, 비우면 q 생략) + 항목 표. q 응답의 warmup·breaker 를 칩·한 줄로.
import { useMemo, useState } from "react";
import { Chip } from "../../components/Chip";
import { Table, type Column } from "../../components/Table";
import { useResource } from "../../lib/hooks";
import { actionLabel } from "../decision/decision.ts";
import { memoryRows, type MemoryResp, type MemoryRow } from "./performance.ts";
import { body } from "./ResourceView";

export function MemoryBlock() {
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const params = useMemo((): Record<string, string> => (q ? { q, limit: "20" } : { limit: "20" }), [q]);
  const mem = useResource<MemoryResp>("/analytics/memory", params, ["memory"]);
  const columns: Column<MemoryRow>[] = [
    { key: "decided_at", label: "결정일", width: "12%", render: (r) => r.decided_at },
    { key: "ticker", label: "종목", width: "12%", render: (r) => `${r.ticker} · ${r.market}` },
    { key: "action", label: "판정", width: "10%", align: "chip", render: (r) => <Chip>{r.action === "—" ? "—" : actionLabel(r.action.toUpperCase())}</Chip> },
    { key: "outcome", label: "결과", width: "10%", align: "num", render: (r) => r.outcome },
    { key: "tier", label: "단계", width: "10%", align: "chip", render: (r) => <Chip>{r.tier}</Chip> },
    { key: "importance", label: "중요도", width: "10%", align: "num", render: (r) => (r.importance === null ? "—" : r.importance.toFixed(2)) },
    { key: "summary", label: "상황 요약", width: "36%", long: true, render: (r) => r.summary || "—" },
  ];
  return (
    <>
      <form
        className="perf-filters"
        onSubmit={(e) => {
          e.preventDefault();
          setQ(text.trim());
        }}
      >
        <input aria-label="종목 검색" placeholder="종목 코드" value={text} onChange={(e) => setText(e.target.value)} />
        <button type="submit" className="btn">
          검색
        </button>
      </form>
      {body(mem, "복기 메모리", (d) => (
        <>
          {d.warmup || d.breaker ? (
            <p className="perf-note">
              {d.warmup ? <Chip tone="warn">워밍업</Chip> : null} {d.breaker ? <span>차단: {d.breaker}</span> : null}
            </p>
          ) : null}
          <Table columns={columns} rows={memoryRows(d)} rowKey={(r) => r.id} empty="복기 기록 없음" />
        </>
      ))}
    </>
  );
}
