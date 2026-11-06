// 「오늘」 화면 — 페이지 2개(nav.ts pages). 현황(`/`): 날짜 → 대기 승인(최상단) → 오늘 요약 KPI → 레인 카드(현물·단타·파생) →
// 아침 파이프라인(시장별) → 결정 → 리스크 게이트(0건이면 hidden).
// 기록(`/log`): 주문·체결 → 다음 할 일 → 알림. 날짜는 `?date=` 로 두 페이지가 같이 쓴다(없으면 오늘을 따라가 KST 자정에 넘어감).
// 섹션 컨테이너(id = NAV 항목)는 데이터 전에 바로 그린다(메뉴의 IntersectionObserver 가 마운트 시점 DOM 만 본다).
// 계좌 없는 레인·대기 승인 없음은 언마운트하지 않고 hidden 으로 둔다(메뉴 앵커가 계속 가리킬 수 있게).
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { DatePicker } from "../../components/DatePicker";
import { Empty } from "../../components/Empty";
import { PageTabs } from "../../components/PageTabs";
import { Stats } from "../../components/Stats";
import { useKstDate, useResource } from "../../lib/hooks";
import type { NavPage } from "../../lib/nav";
import type { UsageResp } from "../../lib/usage";
import { ApprovalsBlock, type Approval } from "./ApprovalsBlock";
import { DecisionsList } from "./DecisionsList";
import { FoLaneExtra, LaneCardView, type LaneFetch } from "./LaneCards";
import { NextJobs } from "./NextJobs";
import { NoticesFeed } from "./NoticesFeed";
import { OrdersTimeline } from "./OrdersTimeline";
import { Pipeline } from "./Pipeline";
import { RiskChecks } from "./RiskChecks";
import type { MarketToday, RiskCheck } from "./morning.ts";
import {
  LANE_LABEL,
  LANES,
  foLaneInfo,
  laneCards,
  laneNextAt,
  todaySummary,
  type Account,
  type Daemon,
  type Decision,
  type FoToday,
  type Merged,
  type Notice,
  type Order,
} from "./today.ts";

export type TodayPage = NavPage["id"];

export function Today({ page = "today" }: { page?: TodayPage }) {
  const log = page === "log";
  // ?date= 가 없으면 「오늘」을 따라간다(KST 자정에 넘어감). 사용자가 다른 날을 고르면 주소에 남아 두 페이지가 같이 쓴다.
  const today = useKstDate();
  const [params, setParams] = useSearchParams();
  const picked = params.get("date");
  const date = picked ?? today;
  const day = { date };
  const setDate = (v: string) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (v && v !== today) next.set("date", v);
        else next.delete("date");
        return next;
      },
      { replace: true },
    );
  const search = picked ? `?date=${picked}` : "";
  const approvals = useResource<{ approvals: Approval[] }>("/approvals", undefined, ["approvals", "orders"], { enabled: !log });
  const decisions = useResource<Merged<Decision>>("/decisions", day, ["decisions", "scalp", "fo"], { enabled: !log });
  const orders = useResource<Merged<Order>>("/orders", day, ["orders", "scalp", "fo"]);
  const accounts = useResource<Account[]>("/accounts", undefined, ["accounts"], { enabled: !log });
  const usage = useResource<UsageResp>("/system/usage", undefined, ["usage"], { enabled: !log });
  const [noticeKind, setNoticeKind] = useState<string | null>(null);
  const notices = useResource<Notice[]>("/system/notices", noticeKind ? { limit: "200", kind: noticeKind } : { limit: "200" }, ["notices"]);
  const daemon = useResource<Daemon>("/system/daemon", undefined, ["jobs", "runs", "daemon"]); // 현황은 레인 「다음 예정」, 기록은 「다음 할 일」
  const pipeKr = useResource<MarketToday>("/markets/KR/today", day, ["decisions", "jobs"], { enabled: !log });
  const pipeUs = useResource<MarketToday>("/markets/US/today", day, ["decisions", "jobs"], { enabled: !log });
  const riskChecks = useResource<RiskCheck[]>("/risk-checks", day, ["decisions", "orders"], { enabled: !log });
  const hasFo = !!accounts.data?.some((a) => a.lane === "fo");
  const foToday = useResource<FoToday>("/fo/today", day, ["fo", "decisions"], { enabled: !log && hasFo });

  const pending = approvals.data?.approvals ?? [];
  const cards = accounts.data
    ? laneCards(accounts.data, decisions.data?.items ?? [], orders.data?.items ?? [], usage.data?.caps_by_kind ?? [])
    : null;
  const laneFetch: LaneFetch =
    decisions.error || orders.error ? "error" : !decisions.data || !orders.data ? "loading" : "ready";
  const jobs = daemon.data?.jobs ?? [];
  const summary = laneFetch === "ready" ? todaySummary(decisions.data?.items ?? [], orders.data?.items ?? [], notices.data ?? []) : null;
  const n = (v: number | undefined) => (summary ? String(v) : "—");

  if (log) {
    return (
      <div className="screen" data-screen="today" data-page="log">
        <header className="screen-head">
          <h1 className="screen-title">오늘 · 기록</h1>
          <DatePicker value={date} onChange={(v) => v && setDate(v)} max={today} />
        </header>
        <PageTabs screen="today" label="오늘 페이지" search={search} />

        <section id="orders-timeline" className="screen-section">
          <h2>주문·체결</h2>
          <OrdersTimeline data={orders.data} error={orders.error} />
        </section>

        <section id="next-jobs" className="screen-section">
          <h2>다음 할 일</h2>
          <NextJobs data={daemon.data} error={daemon.error} />
        </section>

        <section id="notices" className="screen-section">
          <h2>알림</h2>
          <NoticesFeed data={notices.data} error={notices.error} kind={noticeKind} onKind={setNoticeKind} />
        </section>
      </div>
    );
  }

  return (
    <div className="screen" data-screen="today" data-page="today">
      <header className="screen-head">
        <h1 className="screen-title">오늘</h1>
        <DatePicker value={date} onChange={(v) => v && setDate(v)} max={today} />
      </header>
      <PageTabs screen="today" label="오늘 페이지" search={search} />

      <section id="approvals" className="screen-section" hidden={!pending.length && !approvals.error}>
        <h2>승인 대기 {pending.length}건</h2>
        {approvals.error ? <Empty title="승인 대기를 불러오지 못했습니다">{approvals.error.message}</Empty> : null}
        <ApprovalsBlock items={pending} accounts={accounts.data ?? []} onDone={approvals.reload} />
      </section>

      <div className="today-summary" aria-label="오늘 요약">
        <Stats
          size="kpi"
          cols="auto"
          items={[
            { id: "decisions", label: "결정", value: n(summary?.decisions) },
            { id: "orders", label: "주문", value: n(summary?.orders) },
            { id: "fills", label: "체결", value: n(summary?.fills) },
            { id: "failed", label: "실패·거절", value: n(summary?.failed) },
            { id: "notices", label: "알림", value: notices.data ? String(notices.data.length) : "—" },
          ]}
        />
      </div>

      <div className="lane-grid">
        {LANES.map((lane) => {
          const card = cards?.find((c) => c.lane === lane);
          return (
            <section key={lane} id={`${lane}-lane`} className="screen-section" hidden={!!cards && !card} aria-label={LANE_LABEL[lane]}>
              {card ? (
                <LaneCardView
                  card={card}
                  fetch={laneFetch}
                  nextAt={laneNextAt(lane, jobs)}
                  extra={lane === "fo" && foToday.data ? <FoLaneExtra info={foLaneInfo(foToday.data)} /> : null}
                />
              ) : accounts.error ? (
                <Empty title="계좌를 불러오지 못했습니다">{accounts.error.message}</Empty>
              ) : (
                <Empty title={`${LANE_LABEL[lane]} 불러오는 중`} />
              )}
            </section>
          );
        })}
      </div>

      <section id="pipeline" className="screen-section">
        <h2>아침 파이프라인</h2>
        <Pipeline kr={pipeKr} us={pipeUs} />
      </section>

      <section id="decisions" className="screen-section">
        <h2>결정</h2>
        {decisions.error ? (
          <Empty title="결정을 불러오지 못했습니다">{decisions.error.message}</Empty>
        ) : (
          <DecisionsList items={decisions.data?.items ?? []} partial={!!decisions.data?.partial} date={date} />
        )}
      </section>

      <section id="risk-checks" className="screen-section" hidden={!!riskChecks.data && !riskChecks.data.length}>
        <h2>리스크 게이트</h2>
        <RiskChecks data={riskChecks.data} error={riskChecks.error} />
      </section>

    </div>
  );
}
