// 상단 띠 시계 — 서버 보정 시각을 1초마다, 서울 시각·미 동부 시각(괄호 ET 병기, 스펙)과 장 세션.
import { useNow } from "../../lib/hooks";
import { clockText } from "../../lib/clock";

export function Clock() {
  const c = clockText(useNow(1000));
  return (
    <div className="clock" title={c.phase}>
      <span className="num">
        <span className="clock-date">{c.date} </span>
        <strong>{c.kst}</strong> 서울
      </span>
      <span className="num clock-et">
        {c.etDate} {c.et} (ET)
      </span>
      <span className="clock-phase">{c.phase}</span>
    </div>
  );
}
