// 알림 — 최근 200건(최신 순)을 30행씩 표(시각·종류·계좌·내용·전송)로, kind 필터 칩(한글), 전송 실패(ok=false) 칩.
// 필터를 걸면 응답에 그 kind 만 오므로 칩 목록은 지금까지 본 kind 를 모아 둔다.
import { useState } from "react";
import { Chip } from "../../components/Chip";
import { Empty } from "../../components/Empty";
import { Pager } from "../../components/Pager";
import { Table, type Column } from "../../components/Table";
import type { ApiError } from "../../lib/api";
import { fmtWhen } from "../../lib/format";
import { usePaged } from "../../lib/usePaged";
import { noticeKindLabel, noticeKinds, noticeRows, type Notice, type NoticeRow } from "./today.ts";

type Props = { data: Notice[] | null; error: ApiError | null; kind: string | null; onKind(kind: string | null): void };

const COLUMNS: Column<NoticeRow>[] = [
  { key: "at", label: "시각", width: "12%", align: "num", render: (n) => fmtWhen(n.at) },
  { key: "title", label: "종류", width: "14%", align: "chip", render: (n) => <Chip>{n.title}</Chip> },
  { key: "account_id", label: "계좌", width: "12%", render: (n) => n.account_id ?? "—" },
  { key: "body", label: "내용", width: "auto", long: true, render: (n) => <span title={n.full}>{n.body || n.full}</span> },
  { key: "ok", label: "전송", width: "10%", align: "chip", render: (n) => (n.ok === false ? <Chip tone="fail">전송 실패</Chip> : "") },
];

/** 30행씩 ?page=N — body 콜백 밖에서 훅을 쓰려고 부품으로. */
function PagedNotices({ rows }: { rows: NoticeRow[] }) {
  const paged = usePaged(rows);
  return (
    <>
      <Table columns={COLUMNS} rows={paged.rows} rowKey={(n) => n.id} empty="알림이 없습니다" />
      <Pager {...paged} />
    </>
  );
}

export function NoticesFeed({ data, error, kind, onKind }: Props) {
  const [known, setKnown] = useState<string[]>([]);
  const fresh = noticeKinds([...(data ?? []), ...(kind ? [{ kind }] : [])]).filter((k) => !known.includes(k));
  if (fresh.length) setKnown([...known, ...fresh]); // 렌더 중 자기 상태 갱신(조건부, 한 번 더 그린다)
  const kinds = [...known, ...fresh];

  return (
    <>
      <div className="notice-filters" role="group" aria-label="알림 종류">
        {[null, ...kinds].map((k) => (
          <button
            key={k ?? ""}
            type="button"
            className="chip notice-filter"
            data-notice-kind={k ?? ""}
            aria-pressed={kind === k}
            onClick={() => onKind(k)}
          >
            {k ? noticeKindLabel(k) : "전체"}
          </button>
        ))}
      </div>
      {error ? <Empty title="알림을 불러오지 못했습니다">{error.message}</Empty> : !data ? <Empty title="불러오는 중" /> : <PagedNotices rows={noticeRows(data)} />}
    </>
  );
}
