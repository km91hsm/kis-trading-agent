// 테마 — GET /news/themes?market= 순위. 카드마다 점수·촉매 확인·구성 종목 수, 대표 종목(이름)과 근거.
import { useMemo, useState } from "react";
import { Card } from "../../components/Card";
import { Chip } from "../../components/Chip";
import { Empty } from "../../components/Empty";
import { useResource } from "../../lib/hooks";
import { body } from "../performance/ResourceView";
import { MarketSelect, type Market } from "./Data.tsx";
import { themeRows, type ThemesResp } from "./data.ts";

export function Themes() {
  const [market, setMarket] = useState<Market>("KR");
  const params = useMemo(() => ({ market }), [market]);
  const res = useResource<ThemesResp>("/news/themes", params, ["theme"]);
  return (
    <>
      <div className="perf-filters">
        <MarketSelect value={market} onChange={setMarket} />
      </div>
      {body(res, "테마", (d) => {
        const rows = themeRows(d);
        if (!rows.length) return <Empty title="테마 없음" />;
        return (
          <div className="data-themes">
            {rows.map((t) => (
              <Card
                key={t.theme}
                title={t.theme}
                right={
                  <span className="chips">
                    <Chip>점수 {t.score.toFixed(2)}</Chip>
                    {t.catalystOk ? <Chip tone="ok">촉매</Chip> : null}
                    <Chip>{t.members}종목</Chip>
                  </span>
                }
              >
                <ul className="data-theme-tickers">
                  {t.tickers.map((x) => (
                    <li key={x.ticker}>
                      {x.name} <span className="data-muted">{x.ticker}</span>
                      {x.why.length ? <span className="data-muted"> · {x.why.join(", ")}</span> : null}
                    </li>
                  ))}
                </ul>
              </Card>
            ))}
          </div>
        );
      })}
    </>
  );
}
