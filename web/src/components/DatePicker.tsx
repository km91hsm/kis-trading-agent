// 날짜 고르기 — <input type="date"> + 「오늘」(서버 보정 시각의 KST 날짜).
import { now } from "../lib/clock";
import { kstDate } from "../lib/format";

export function DatePicker({ value, onChange, max }: { value: string; onChange(v: string): void; max?: string }) {
  return (
    <span className="date-picker">
      <input type="date" aria-label="날짜" value={value} max={max} onChange={(e) => onChange(e.target.value)} />
      <button type="button" className="btn" onClick={() => onChange(kstDate(now()))}>
        오늘
      </button>
    </span>
  );
}
