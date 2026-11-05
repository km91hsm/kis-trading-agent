// 옛 `web/src/format.ts` 이식
// 숫자·시각 표시. 금액은 API 의 Decimal 문자열을 그대로 받고(단위는 표 머리글), 시각은 KST(미국 장은 ET).

const DASH = "—";
const KST = "Asia/Seoul";

function num(v: string | number | null | undefined): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

function fixed2(v: string | number | null | undefined): string {
  const n = num(v);
  if (n === null) return DASH;
  return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** 원화: 정수 천 단위 "1,234,567". */
export function fmtKrw(v: string | number | null | undefined): string {
  const n = num(v);
  if (n === null) return DASH;
  return (Math.round(n) || 0).toLocaleString("en-US"); // || 0: -0 을 "0" 으로
}

/** 달러: 소수 둘째 "1,234.50". */
export function fmtUsd(v: string | number | null | undefined): string {
  return fixed2(v);
}

export function fmtMoney(v: string | number | null | undefined, currency: "KRW" | "USD"): string {
  return currency === "USD" ? fmtUsd(v) : fmtKrw(v);
}

/** 비율(0.0153) → "1.53%". 이미 퍼센트 단위면 호출 전에 /100 한다. */
export function fmtPct(v: string | number | null | undefined, digits = 2): string {
  const n = num(v);
  if (n === null) return DASH;
  return `${(n * 100).toFixed(digits)}%`;
}

/** 파생 지수 포인트: 소수 둘째. */
export function fmtPt(v: string | number | null | undefined): string {
  return fixed2(v);
}

function parse(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

const timeIn = (d: Date, timeZone: string, seconds: boolean) =>
  new Intl.DateTimeFormat("en-GB", {
    timeZone,
    hour: "2-digit",
    minute: "2-digit",
    ...(seconds ? { second: "2-digit" } : {}),
    hourCycle: "h23",
  }).format(d);

/** KST "HH:MM:SS". */
export function fmtTime(iso: string | null | undefined): string {
  const d = parse(iso);
  return d ? timeIn(d, KST, true) : DASH;
}

/** 뉴욕 "HH:MM ET". */
export function fmtTimeEt(iso: string | null | undefined): string {
  const d = parse(iso);
  return d ? `${timeIn(d, "America/New_York", false)} ET` : DASH;
}

/** Date → KST "YYYY-MM-DD". */
export function kstDate(now: Date): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: KST, year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}

/** ISO 시각 → KST 날짜 "YYYY-MM-DD". 이미 날짜 문자열이면 그대로. */
export function fmtDate(iso: string | null | undefined): string {
  if (iso && /^\d{4}-\d{2}-\d{2}$/.test(iso)) return iso;
  const d = parse(iso);
  return d ? kstDate(d) : DASH;
}

const kstMmDd = (d: Date) => new Intl.DateTimeFormat("en-US", { timeZone: KST, month: "2-digit", day: "2-digit" }).format(d).replace("/", ".");

/** 화면 공통 시각 표기(KST): 오늘이면 「HH:MM」, 다른 날이면 「MM.DD HH:MM」, 날짜만 있는 값은 「MM.DD」. */
export function fmtWhen(iso: string | null | undefined, now: Date = new Date()): string {
  if (iso && /^\d{4}-\d{2}-\d{2}$/.test(iso)) return `${iso.slice(5, 7)}.${iso.slice(8, 10)}`;
  const d = parse(iso);
  if (!d) return DASH;
  const hm = timeIn(d, KST, false);
  return kstDate(d) === kstDate(now) ? hm : `${kstMmDd(d)} ${hm}`;
}

/** 「N초 전」·「N분 전」·「N시간 전」·「N일 전」(내림). 미래 시각(시계 어긋남)은 0초. */
export function agoText(iso: string | null | undefined, now: Date): string {
  const d = parse(iso);
  if (!d) return DASH;
  const s = Math.max(0, Math.floor((now.getTime() - d.getTime()) / 1000));
  if (s < 60) return `${s}초 전`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}분 전`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}시간 전`;
  return `${Math.floor(h / 24)}일 전`;
}
