// 옛 `web/src/clock.ts` 이식 (+ 옛 `tape.ts` 의 marketPhase 축약)
// 상단 띠 시계·장 세션 표기, 서버 시각(X-Server-Time) 기준 오프셋 보정.

let offsetMs = 0;

/** 서버 시각 관측. 오프셋은 마지막 관측 1개(평활 없음). 깨진 시각은 무시한다. */
export function noteServerTime(iso: string, clientNow: number = Date.now()): void {
  const t = Date.parse(iso);
  if (Number.isFinite(t)) offsetMs = t - clientNow;
}

/** 서버 − 클라이언트(ms). 양수면 서버가 앞선다. */
export function serverOffsetMs(): number {
  return offsetMs;
}

/** 서버 시각으로 보정한 지금. */
export function now(): Date {
  return new Date(Date.now() + offsetMs);
}

/** |offset| ≥ 1초면 「시계 어긋남 +N초」(반올림), 아니면 null. */
export function clockSkewText(offset: number): string | null {
  if (!Number.isFinite(offset) || Math.abs(offset) < 1000) return null;
  return `시계 어긋남 ${offset > 0 ? "+" : "−"}${Math.round(Math.abs(offset) / 1000)}초`;
}

const KST = "Asia/Seoul";
const ET = "America/New_York";
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function localParts(d: Date, timeZone: string): { weekday: number; minutes: number } {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return { weekday: WEEKDAYS.indexOf(get("weekday")), minutes: Number(get("hour")) * 60 + Number(get("minute")) };
}

/** 평일 [open, close) 분 구간이면 true. 주말이면 null(휴장). 공휴일은 모른다. */
function inSession(d: Date, timeZone: string, open: number, close: number): boolean | null {
  const { weekday, minutes } = localParts(d, timeZone);
  if (weekday === 0 || weekday === 6) return null;
  return minutes >= open && minutes < close;
}

export type MarketPhase = { kr: "정규" | "장외" | "휴장"; us: "정규" | "장외"; fo: "정규" | "장외" };

/** KR 09:00~15:30 KST, US 09:30~16:00 ET, 파생(KRX 주간) 08:45~15:45 KST. 주말만 휴장으로 본다. */
export function marketPhase(now: Date): MarketPhase {
  const kr = inSession(now, KST, 9 * 60, 15 * 60 + 30);
  const us = inSession(now, ET, 9 * 60 + 30, 16 * 60);
  const fo = inSession(now, KST, 8 * 60 + 45, 15 * 60 + 45);
  return { kr: kr === null ? "휴장" : kr ? "정규" : "장외", us: us ? "정규" : "장외", fo: fo ? "정규" : "장외" };
}

const KO_WEEKDAY = ["일", "월", "화", "수", "목", "금", "토"];

/** "10.26 (월)" — 그 시간대의 날짜·요일(뉴욕은 한국보다 하루 늦을 때가 많다). */
function dateIn(now: Date, timeZone: string): string {
  const md = new Intl.DateTimeFormat("en-CA", { timeZone, month: "2-digit", day: "2-digit" }).format(now); // "10-26"
  const { weekday } = localParts(now, timeZone);
  return `${md.replace("-", ".")} (${KO_WEEKDAY[weekday] ?? ""})`;
}

const hms = (d: Date, timeZone: string) =>
  new Intl.DateTimeFormat("en-GB", { timeZone, hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }).format(d);

export type ClockText = { date: string; kst: string; et: string; etDate: string; phase: string };

/** {date: "10.26 (월)", kst: "21:06:05", et: "08:06:05", etDate: "10.26 (월)", phase: "KR 장외 · US 장외 · 파생 장외"}.
 *  뉴욕도 날짜·요일·초까지(옛 사용자 요청 2026-10-27). */
export function clockText(now: Date): ClockText {
  const p = marketPhase(now);
  return {
    date: dateIn(now, KST),
    kst: hms(now, KST),
    et: hms(now, ET),
    etDate: dateIn(now, ET),
    phase: `KR ${p.kr} · US ${p.us} · 파생 ${p.fo}`,
  };
}
