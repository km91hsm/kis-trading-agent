// 「데이터」 화면 — 페이지 4개(nav.ts data.pages, 하위 메뉴마다 한 페이지): 시세·차트 `/data`(종목 검색 → 캔들·체결 표식·손절/목표·
// 현재가선, KR 호가 10단) · 뉴스·공시 `/data/news`(라벨·이름·「판정에 쓰임」, 수동 입력, 공시 중요도) · 테마·감시목록 `/data/themes` ·
// 지표·모델 `/data/features`(정렬 표·이력·활성 모델·잡 버튼). 절이 어느 페이지에 가는지는 nav.ts 항목의 `page` 가 정한다.
// 절 컨테이너는 데이터 전에 바로 그리고(절 id = nav.ts 항목), 절마다 로딩·오류를 따로 보인다.
import { useCallback, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { PageTabs } from "../../components/PageTabs";
import { NAV } from "../../generated/nav";
import { screenPages } from "../../lib/nav";
import { ChartView } from "./ChartView";
import { Features } from "./Features";
import { News } from "./News";
import { Themes } from "./Themes";
import { Watchlist } from "./Watchlist";

export type Market = "KR" | "US";
const MARKETS: Market[] = ["KR", "US"];

/** 시장은 화면 머리의 세그먼트 하나로 고르고 `?market=` 에 남긴다(절마다 select 를 두지 않는다). 기본 KR. */
export function useMarket(): [Market, (m: Market) => void] {
  const [sp, setSp] = useSearchParams();
  const market: Market = sp.get("market") === "US" ? "US" : "KR";
  const set = useCallback(
    (m: Market) =>
      setSp(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (m === "KR") next.delete("market");
          else next.set("market", m);
          next.delete("page"); // 시장을 바꾸면 1쪽부터, 종목 필터도 뜻이 없다
          next.delete("epage");
          next.delete("ticker");
          return next;
        },
        { replace: true },
      ),
    [setSp],
  );
  return [market, set];
}

export function MarketSegment() {
  const [market, setMarket] = useMarket();
  return (
    <div className="segment" role="group" aria-label="시장" data-market-segment>
      {MARKETS.map((m) => (
        <button key={m} type="button" className="segment-btn" aria-pressed={market === m} onClick={() => setMarket(m)}>
          {m}
        </button>
      ))}
    </div>
  );
}

export type DataPage = (typeof NAV.data.pages)[number]["id"];
type SectionId = (typeof NAV.data.items)[number]["id"];

const SECTIONS: Record<SectionId, { title: string; body: ReactNode }> = {
  quotes: { title: "시세·차트", body: <ChartView /> },
  news: { title: "뉴스·공시", body: <News /> },
  themes: { title: "테마", body: <Themes /> },
  watchlist: { title: "감시목록", body: <Watchlist /> },
  features: { title: "지표·모델", body: <Features /> },
};

export function Data({ page = "quotes" }: { page?: DataPage }) {
  const pages = screenPages("data");
  const pageLabel = pages.find((p) => p.id === page)?.label ?? "";
  const ids = NAV.data.items.filter((i) => i.page === page).map((i) => i.id as SectionId);
  return (
    <div className="screen" data-screen="data" data-page={page}>
      <h1 className="screen-title">데이터 · {pageLabel}</h1>
      <PageTabs screen="data" label="데이터 페이지" />
      <MarketSegment />
      {ids.map((id) => (
        <section key={id} id={id} className="screen-section">
          <h2>{SECTIONS[id].title}</h2>
          {SECTIONS[id].body}
        </section>
      ))}
    </div>
  );
}
