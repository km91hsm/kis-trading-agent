// 레인 카드 — 오늘 결정(매수·매도·보류)·주문·체결·미체결·다음 예정(데몬 사용자 잡 next_at)과 상태, 세션 상한. 격자는 Stats 하나.
import { Badge, type Tone } from "../../components/Badge";
import { Card } from "../../components/Card";
import { Chip } from "../../components/Chip";
import { Stats } from "../../components/Stats";
import { fmtWhen } from "../../lib/format";
import { LANE_LABEL, type LaneCard } from "./today.ts";

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

export function LaneCardView({ card, fetch = "ready", nextAt = null }: { card: LaneCard; fetch?: LaneFetch; nextAt?: string | null }) {
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
    </Card>
  );
}
