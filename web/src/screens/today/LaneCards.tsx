// 레인 카드 — 오늘 결정(매수·매도·보류)·주문·체결·미체결과 상태, 세션 상한.
import { Badge, type Tone } from "../../components/Badge";
import { Card } from "../../components/Card";
import { Chip } from "../../components/Chip";
import { LANE_LABEL, type LaneCard } from "./today.ts";

const STATE: Record<LaneCard["state"], { tone: Tone; text: string }> = {
  done: { tone: "ok", text: "완료" },
  waiting: { tone: "warn", text: "진행 중" },
  paused: { tone: "fail", text: "일시정지" },
};

export function LaneCardView({ card }: { card: LaneCard }) {
  const s = STATE[card.state];
  const { buy, sell, hold } = card.decisions;
  return (
    <Card title={LANE_LABEL[card.lane]} right={<Badge tone={s.tone}>{s.text}</Badge>}>
      <dl className="lane-stats">
        <div>
          <dt>결정</dt>
          <dd>
            매수 {buy} · 매도 {sell} · 보류 {hold}
          </dd>
        </div>
        <div>
          <dt>주문</dt>
          <dd>
            {card.orders}건 · 체결 {card.fills} · 미체결 {card.open}
          </dd>
        </div>
      </dl>
      {card.capText ? <Chip>{card.capText}</Chip> : null}
    </Card>
  );
}
