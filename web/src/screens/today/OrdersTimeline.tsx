// 그날 주문·체결 타임라인 — 주문 접수 한 줄 + 체결마다 한 줄(시각 오름차순). `/orders` 는 Today 가 한 번만 부른다.
import { Chip } from "../../components/Chip";
import { Empty } from "../../components/Empty";
import type { ApiError } from "../../lib/api";
import { timeline, type Merged, type Order } from "./today.ts";

export function OrdersTimeline({ data, error }: { data: Merged<Order> | null; error: ApiError | null }) {
  if (error) return <Empty title="주문을 불러오지 못했습니다">{error.message}</Empty>;
  if (!data) return <Empty title="불러오는 중" />;
  const lines = timeline(data.items);
  return (
    <>
      {data.partial ? <Chip tone="warn">일부 레인 응답 없음</Chip> : null}
      {lines.length ? (
        <ol className="timeline">
          {lines.map((l, i) => (
            <li key={`${i}-${l.at}`} className="timeline-row" data-tone={l.tone}>
              <time className="timeline-time" dateTime={l.at}>
                {l.time}
              </time>
              <span className="timeline-text">{l.text}</span>
            </li>
          ))}
        </ol>
      ) : (
        <Empty title="이 날 주문이 없습니다" />
      )}
    </>
  );
}
