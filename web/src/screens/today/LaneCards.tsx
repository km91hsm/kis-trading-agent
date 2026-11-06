// 레인 카드 — 오늘 결정(매수·매도·보류)·주문·체결·미체결·다음 예정(데몬 사용자 잡 next_at)과 상태, 세션 상한. 격자는 Stats 하나.
// 파생 카드는 `FoLaneExtra`(/fo/today: 아침 결정 한 줄·재평가 횟수·계좌 칩 손실 한도/진입 대기)를 덧붙인다.
import type { ReactNode } from "react";
import { Badge, type Tone } from "../../components/Badge";
import { Card } from "../../components/Card";
import { Chip } from "../../components/Chip";
import { Stats } from "../../components/Stats";
import { fmtWhen } from "../../lib/format";
import { LANE_LABEL, type FoLaneInfo, type LaneCard } from "./today.ts";

const STATE: Record<LaneCard["state"], { tone: Tone; text: string }> = {
  done: { tone: "ok", text: "처리 완료" },
  waiting: { tone: "warn", text: "진행 중" },
  paused: { tone: "fail", text: "일시정지" },
};

/** 결정·주문 조회가 끝나지 않았거나(loading) 실패하면(error) 집계 대신 그 상태만 보인다(빈 목록을 「완료」로 오인하지 않게). */
export type LaneFetch = "ready" | "loading" | "error";
const FETCH: Record<Exclude<LaneFetch, "ready">, { tone: Tone; text: string }> = {
  loading: { tone: "muted", text: "불러오는 중" },
  error: { tone: "fail", text: "불러오지 못함" },
};

export function LaneCardView({
  card,
  fetch = "ready",
  nextAt = null,
  extra = null,
}: {
  card: LaneCard;
  fetch?: LaneFetch;
  nextAt?: string | null;
  /** 레인별 덧붙임(파생: 당일 아침 결정·재평가·계좌 플래그). */
  extra?: ReactNode;
}) {
  if (fetch !== "ready") {
    const f = FETCH[fetch];
    return (
      <Card title={LANE_LABEL[card.lane]} right={<Badge tone={f.tone}>{f.text}</Badge>}>
        <p className="lane-note">결정·주문 집계 —</p>
      </Card>
    );
  }
  const s = STATE[card.state];
  const { buy, sell, hold } = card.decisions;
  return (
    <Card title={LANE_LABEL[card.lane]} right={<Badge tone={s.tone}>{s.text}</Badge>}>
      <Stats
        cols={3}
        items={[
          { id: "decisions", label: "결정", value: buy + sell + hold, sub: `매수 ${buy} · 매도 ${sell} · 보류 ${hold}` },
          { id: "orders", label: "주문", value: card.orders, sub: `체결 ${card.fills} · 미체결 ${card.open}` },
          { id: "next", label: "다음 예정", value: nextAt ? fmtWhen(nextAt) : "—" },
        ]}
      />
      {card.capText ? <Chip>{card.capText}</Chip> : null}
      {extra}
    </Card>
  );
}

export function FoLaneExtra({ info }: { info: FoLaneInfo | null }) {
  if (!info || (!info.line && !info.reevals && !info.flags.length)) return null;
  return (
    <div className="lane-extra">
      {info.line ? (
        <p className="lane-note">
          {info.line}
          {info.reevals ? ` · 재평가 ${info.reevals}회` : ""}
        </p>
      ) : info.reevals ? (
        <p className="lane-note">재평가 {info.reevals}회</p>
      ) : null}
      {info.flags.length ? (
        <p className="chips">
          {info.flags.flatMap((f) => [
            f.halted ? <Chip key={`${f.id}:h`} tone="fail">{f.id} 손실 한도</Chip> : null,
            f.pending ? <Chip key={`${f.id}:p`} tone="warn">{f.id} 진입 대기</Chip> : null,
          ])}
        </p>
      ) : null}
    </div>
  );
}
