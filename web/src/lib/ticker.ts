// 종목 표기 한 곳 — 항상 「코드 이름」(이름 없으면 코드만, 코드도 없으면 —). 이름은 백엔드 행의 `name`(원장 ticker_names).
export function tickerLabel(ticker: string | null | undefined, name: string | null | undefined): string {
  if (!ticker) return "—";
  return name ? `${ticker} ${name}` : ticker;
}
