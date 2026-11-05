// 「오늘」 화면 — 대기 승인(최상단)·레인 카드(현물·단타·파생)·그날 결정 목록·주문·체결 타임라인·알림·다음 할 일, 날짜 선택.
// 섹션 컨테이너(id = NAV 항목)는 데이터 전에 바로 그린다(메뉴의 IntersectionObserver 가 마운트 시점 DOM 만 본다).
// 계좌 없는 레인·대기 승인 없음은 언마운트하지 않고 hidden 으로 둔다(메뉴 앵커가 계속 가리킬 수 있게).
import { useState } from "react";
import { DatePicker } from "../../components/DatePicker";
import { Empty } from "../../components/Empty";
import { useKstDate, useResource } from "../../lib/hooks";
import type { UsageResp } from "../../lib/usage";
import { ApprovalsBlock, type Approval } from "./ApprovalsBlock";
import { DecisionsList } from "./DecisionsList";
import { LaneCardView, type LaneFetch } from "./LaneCards";
import { NextJobs } from "./NextJobs";
import { NoticesFeed } from "./NoticesFeed";
import { OrdersTimeline } from "./OrdersTimeline";
import { LANE_LABEL, LANES, laneCards, type Account, type Daemon, type Decision, type Merged, type Notice, type Order } from "./today.ts";

export function Today() {
  // picked = null 이면 「오늘」을 따라간다(KST 자정에 넘어감). 사용자가 다른 날을 고르면 그 날에 머문다.
  const today = useKstDate();
  const [picked, setPicked] = useState<string | null>(null);
  const date = picked ?? today;
  const day = { date };
  const approvals = useResource<{ approvals: Approval[] }>("/approvals", undefined, ["approvals", "orders"]);
  const decisions = useResource<Merged<Decision>>("/decisions", day, ["decisions", "scalp", "fo"]);
  const orders = useResource<Merged<Order>>("/orders", day, ["orders", "scalp", "fo"]);
  const accounts = useResource<Account[]>("/accounts", undefined, ["accounts"]);
  const usage = useResource<UsageResp>("/system/usage", undefined, ["usage"]);
  const [noticeKind, setNoticeKind] = useState<string | null>(null);
  const notices = useResource<Notice[]>("/system/notices", noticeKind ? { limit: "50", kind: noticeKind } : { limit: "50" }, ["notices"]);
  const daemon = useResource<Daemon>("/system/daemon", undefined, ["jobs", "runs", "daemon"]);

  const pending = approvals.data?.approvals ?? [];
  const cards = accounts.data
    ? laneCards(accounts.data, decisions.data?.items ?? [], orders.data?.items ?? [], usage.data?.caps_by_kind ?? [])
    : null;
  const laneFetch: LaneFetch =
    decisions.error || orders.error ? "error" : !decisions.data || !orders.data ? "loading" : "ready";

  return (
    <div className="screen" data-screen="today">
      <header className="screen-head">
        <h1 className="screen-title">오늘</h1>
        <DatePicker value={date} onChange={(v) => v && setPicked(v === today ? null : v)} max={today} />
      </header>

      <section id="approvals" className="screen-section" hidden={!pending.length && !approvals.error}>
        <h2>승인 대기 {pending.length}건</h2>
        {approvals.error ? <Empty title="승인 대기를 불러오지 못했습니다">{approvals.error.message}</Empty> : null}
        <ApprovalsBlock items={pending} accounts={accounts.data ?? []} onDone={approvals.reload} />
      </section>

      <div className="lane-grid">
        {LANES.map((lane) => {
          const card = cards?.find((c) => c.lane === lane);
          return (
            <section key={lane} id={`${lane}-lane`} className="screen-section" hidden={!!cards && !card} aria-label={LANE_LABEL[lane]}>
              {card ? (
                <LaneCardView card={card} fetch={laneFetch} />
              ) : accounts.error ? (
                <Empty title="계좌를 불러오지 못했습니다">{accounts.error.message}</Empty>
              ) : (
                <Empty title={`${LANE_LABEL[lane]} 불러오는 중`} />
              )}
            </section>
          );
        })}
      </div>

      <section id="decisions" className="screen-section">
        <h2>결정</h2>
        {decisions.error ? (
          <Empty title="결정을 불러오지 못했습니다">{decisions.error.message}</Empty>
        ) : (
          <DecisionsList items={decisions.data?.items ?? []} partial={!!decisions.data?.partial} date={date} />
        )}
      </section>

      <div className="section-pair">
        <section id="orders-timeline" className="screen-section">
          <h2>주문·체결</h2>
          <OrdersTimeline data={orders.data} error={orders.error} />
        </section>

        <section id="notices" className="screen-section">
          <h2>알림</h2>
          <NoticesFeed data={notices.data} error={notices.error} kind={noticeKind} onKind={setNoticeKind} />
        </section>
      </div>

      <section id="next-jobs" className="screen-section">
        <h2>다음 할 일</h2>
        <NextJobs data={daemon.data} error={daemon.error} />
      </section>
    </div>
  );
}
