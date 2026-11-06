// 테마 — GET /news/themes?market=(화면 머리 세그먼트) 순위. 카드마다 점수·촉매 확인·구성 종목 수, 대표 종목 표(코드+이름·근거).
import { useMemo } from "react";
import { Card } from "../../components/Card";
import { Chip } from "../../components/Chip";
import { Empty } from "../../components/Empty";
import { Table, type Column } from "../../components/Table";
import { useResource } from "../../lib/hooks";
import { tickerLabel } from "../../lib/ticker";
import { body } from "../performance/ResourceView";
import { useMarket } from "./Data.tsx";
import { themeRows, type ThemeRow, type ThemesResp } from "./data.ts";

type ThemeTicker = ThemeRow["tickers"][number];
const TICKER_COLUMNS: Column<ThemeTicker>[] = [
  { key: "ticker", label: "종목", width: "40%", render: (x) => tickerLabel(x.ticker, x.name === x.ticker ? null : x.name) },
  { key: "why", label: "근거", width: "60%", long: true, render: (x) => x.why.join(", ") || "—" },
];

export function Themes() {
  const [market] = useMarket();
  const params = useMemo(() => ({ market }), [market]);
  const res = useResource<ThemesResp>("/news/themes", params, ["theme"]);
  return (
    <>
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
                <Table columns={TICKER_COLUMNS} rows={t.tickers} rowKey={(x) => x.ticker} empty="대표 종목 없음" />
              </Card>
            ))}
          </div>
        );
      })}
    </>
  );
}
