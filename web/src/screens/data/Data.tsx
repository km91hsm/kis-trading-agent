// 「데이터」 화면 — `/data`. 시세·차트(종목 검색 → 캔들·체결 표식·손절/목표·현재가선, KR 호가 10단), 뉴스·이벤트(라벨·이름·
// 「판정에 쓰임」, 수동 입력, 공시 중요도), 테마·감시목록, 지표·모델(정렬 표·이력·활성 모델·잡 버튼).
// 절 컨테이너는 데이터 전에 바로 그리고(메뉴 앵커 quotes·news·themes·features), 절마다 로딩·오류를 따로 보인다.
import { ChartView } from "./ChartView";
import { Features } from "./Features";
import { News } from "./News";
import { Themes } from "./Themes";
import { Watchlist } from "./Watchlist";

export type Market = "KR" | "US";

export function MarketSelect({ value, onChange }: { value: Market; onChange(m: Market): void }) {
  return (
    <select aria-label="시장" value={value} onChange={(e) => onChange(e.target.value as Market)}>
      <option value="KR">KR</option>
      <option value="US">US</option>
    </select>
  );
}

export function Data() {
  return (
    <div className="screen" data-screen="data">
      <h1 className="screen-title">데이터</h1>

      <section id="quotes" className="screen-section">
        <h2>시세·차트</h2>
        <ChartView />
      </section>

      <section id="news" className="screen-section">
        <h2>뉴스·이벤트</h2>
        <News />
      </section>

      <section id="themes" className="screen-section">
        <h2>테마</h2>
        <Themes />
      </section>

      <section id="watchlist" className="screen-section">
        <h2>감시목록</h2>
        <Watchlist />
      </section>

      <section id="features" className="screen-section">
        <h2>지표·모델</h2>
        <Features />
      </section>
    </div>
  );
}
