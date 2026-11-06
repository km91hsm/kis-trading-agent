// lightweight-charts(5.x) 래퍼 — 선 시리즈 + 표식(markers) + 가격선(lines). 폭은 ResizeObserver 로 맞추고
// 언마운트 때 chart.remove(). 차트는 한 번만 만들고 props 변화는 setData·setMarkers·가격선 재생성으로 반영한다.
// `extra` 는 비교용 선 시리즈(성과 화면 벤치마크) — 배열이 바뀌면 옛 시리즈를 떼고 다시 붙인다(메모해서 넘길 것).
// `candles` 를 주면 본 시리즈가 선 대신 캔들(상승 --up·하락 --down)이 된다 — 종류는 마운트 때 정해진다.
import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";

export type ChartPoint = { time: string; value: number };
export type ChartMarker = { time: string | number; text: string; color: string };
export type ChartLine = { price: number; label: string; color: string };
export type ChartExtra = { label: string; data: ChartPoint[] };
/** time 은 일봉이면 "YYYY-MM-DD", 분봉이면 epoch 초. */
export type ChartCandle = { time: string | number; open: number; high: number; low: number; close: number };

export type ChartProps = {
  series?: ChartPoint[];
  candles?: ChartCandle[];
  markers?: ChartMarker[];
  lines?: ChartLine[];
  extra?: ChartExtra[];
  height?: number;
  /** 분봉 — 시간축에 시각을 보인다. */
  intraday?: boolean;
};

const EXTRA_COLORS = ["--fg-muted", "--warn", "--ok"];

/** 가격축 숫자 — 백만 이상은 「1,235만」, 억 이상은 「1.5억」(원화 NAV 축이 10,000,000.00 처럼 길어지지 않게), 작은 값은 소수 둘째까지. */
export function fmtAxis(v: number): string {
  const a = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (a >= 1e8) return `${sign}${(a / 1e8).toLocaleString("en-US", { maximumFractionDigits: 1 })}억`;
  if (a >= 1e6) return `${sign}${Math.round(a / 1e4).toLocaleString("en-US")}만`;
  return v.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function cssVar(name: string): string | undefined {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || undefined;
}

/** 점이 하나도 없으면 차트를 만들지 않고 같은 높이의 빈 틀에 「자료 없음」(점 1개짜리 수평선·가짜 축을 그리지 않는다).
 *  자료가 들어오면 LiveChart 가 그때 마운트되어 차트를 만든다. */
export function Chart(props: ChartProps) {
  const { series, candles, extra, height = 240 } = props;
  const points = candles !== undefined ? candles.length : (series ?? []).length;
  if (points === 0 && !extra?.length) {
    return (
      <div className="chart chart-empty" data-chart-empty style={{ height }}>
        자료 없음
      </div>
    );
  }
  // 선 시리즈 점이 하나면(벤치마크 선이 있어도) 평평한 선·가짜 축이 된다 — 내일부터 추이가 생긴다고 알린다.
  if (candles === undefined && points === 1) {
    return (
      <div className="chart chart-empty" data-chart-empty style={{ height }}>
        자료 1일 — 추이는 내일부터
      </div>
    );
  }
  // 값이 전부 같아도 평평한 선에 10000000.05/9999999.95 같은 가짜 축이 생긴다 — 변동 없음을 글로.
  if (candles === undefined && series && points >= 2 && !extra?.length && series.every((p) => p.value === series[0].value)) {
    return (
      <div className="chart chart-empty" data-chart-empty style={{ height }}>
        자료 {points}일 — 변동 없음 ({series[0].value.toLocaleString("en-US")})
      </div>
    );
  }
  return <LiveChart {...props} />;
}

function LiveChart({ series, candles, markers, lines, extra, height = 240, intraday = false }: ChartProps) {
  const el = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const lineRef = useRef<ISeriesApi<"Line"> | ISeriesApi<"Candlestick"> | null>(null);
  const candleMode = useRef(candles !== undefined);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);

  useEffect(() => {
    const host = el.current!;
    const chart = createChart(host, {
      width: host.clientWidth,
      height,
      layout: { background: { color: "transparent" }, textColor: cssVar("--fg-muted"), fontFamily: cssVar("--font") },
      localization: { priceFormatter: fmtAxis },
      grid: { vertLines: { color: cssVar("--line") }, horzLines: { color: cssVar("--line") } },
    });
    chartRef.current = chart;
    if (candleMode.current) {
      const up = cssVar("--up");
      const down = cssVar("--down");
      lineRef.current = chart.addSeries(CandlestickSeries, { upColor: up, downColor: down, wickUpColor: up, wickDownColor: down, borderVisible: false });
    } else {
      lineRef.current = chart.addSeries(LineSeries, { color: cssVar("--accent"), lineWidth: 2 });
    }
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect.width;
      if (w) chart.applyOptions({ width: Math.floor(w) });
    });
    ro.observe(host);
    return () => {
      ro.disconnect();
      chart.remove();
      chartRef.current = null;
      lineRef.current = null;
      markersRef.current = null;
    };
    // 높이 변화는 아래 effect 가 applyOptions 로 반영한다 — 차트를 다시 만들지 않는다.
  }, []);

  useEffect(() => {
    chartRef.current?.applyOptions({ height });
  }, [height]);

  useEffect(() => {
    chartRef.current?.applyOptions({ timeScale: { timeVisible: intraday, secondsVisible: false } });
  }, [intraday]);

  useEffect(() => {
    const s = lineRef.current;
    if (!s) return;
    if (candleMode.current) (s as ISeriesApi<"Candlestick">).setData((candles ?? []).map((c) => ({ ...c, time: c.time as Time })));
    else (s as ISeriesApi<"Line">).setData((series ?? []).map((p) => ({ time: p.time as Time, value: p.value })));
    chartRef.current?.timeScale().fitContent();
  }, [series, candles]);

  useEffect(() => {
    const s = lineRef.current;
    if (!s) return;
    const ms: SeriesMarker<Time>[] = (markers ?? []).map((m) => ({
      time: m.time as Time,
      position: "aboveBar",
      shape: "circle",
      color: m.color,
      text: m.text,
    }));
    if (markersRef.current) markersRef.current.setMarkers(ms);
    else if (ms.length) markersRef.current = createSeriesMarkers(s, ms);
  }, [markers]);

  useEffect(() => {
    const s = lineRef.current;
    if (!s || !lines?.length) return;
    const made = lines.map((l) => s.createPriceLine({ price: l.price, title: l.label, color: l.color, lineWidth: 1 }));
    return () => {
      if (lineRef.current === s) for (const p of made) s.removePriceLine(p);
    };
  }, [lines]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !extra?.length) return;
    const made = extra.map((x, i) => {
      const s = chart.addSeries(LineSeries, { color: cssVar(EXTRA_COLORS[i % EXTRA_COLORS.length]), lineWidth: 1, title: x.label });
      s.setData(x.data.map((p) => ({ time: p.time as Time, value: p.value })));
      return s;
    });
    chart.timeScale().fitContent();
    return () => {
      if (chartRef.current === chart) for (const s of made) chart.removeSeries(s);
    };
  }, [extra]);

  return <div ref={el} className="chart" style={{ height }} />;
}
