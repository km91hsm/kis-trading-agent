import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const m = vi.hoisted(() => {
  const series = { setData: vi.fn(), createPriceLine: vi.fn(() => ({})), removePriceLine: vi.fn() };
  const markersApi = { setMarkers: vi.fn(), detach: vi.fn() };
  const chart = {
    addSeries: vi.fn(() => series),
    removeSeries: vi.fn(),
    applyOptions: vi.fn(),
    remove: vi.fn(),
    timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
  };
  return {
    series,
    markersApi,
    chart,
    createChart: vi.fn(() => chart),
    createSeriesMarkers: vi.fn(() => markersApi),
    LineSeries: { type: "Line" },
    CandlestickSeries: { type: "Candlestick" },
  };
});
vi.mock("lightweight-charts", () => ({
  createChart: m.createChart,
  createSeriesMarkers: m.createSeriesMarkers,
  LineSeries: m.LineSeries,
  CandlestickSeries: m.CandlestickSeries,
}));

import { Chart } from "../Chart";
import { render } from "../../test/render";

let roCallback: ResizeObserverCallback | null = null;
const disconnect = vi.fn();
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(cb: ResizeObserverCallback) {
        roCallback = cb;
      }
      observe() {}
      unobserve() {}
      disconnect = disconnect;
    },
  );
});
afterEach(() => vi.unstubAllGlobals());

describe("Chart", () => {
  test("컨테이너로 createChart, LineSeries 로 addSeries, 시리즈를 setData", () => {
    const { container } = render(
      <Chart
        series={[{ time: "2026-11-04", value: 100 }, { time: "2026-11-05", value: 101.5 }]}
        markers={[{ time: "2026-11-05", text: "매수", color: "red" }]}
        lines={[{ price: 99, label: "손절", color: "blue" }]}
        height={200}
      />,
    );
    const el = container.querySelector(".chart") as HTMLElement;
    expect(m.createChart).toHaveBeenCalledTimes(1);
    expect((m.createChart.mock.calls[0] as unknown[])[0]).toBe(el);
    expect((m.createChart.mock.calls[0] as unknown[])[1]).toMatchObject({ height: 200 });
    expect((m.chart.addSeries.mock.calls[0] as unknown[])[0]).toBe(m.LineSeries);
    expect(m.series.setData).toHaveBeenLastCalledWith([
      { time: "2026-11-04", value: 100 },
      { time: "2026-11-05", value: 101.5 },
    ]);
    expect((m.createSeriesMarkers.mock.calls[0] as unknown[])[0]).toBe(m.series);
    const markers = (m.createSeriesMarkers.mock.calls[0] as unknown[])[1] as Record<string, unknown>[];
    expect(markers[0]).toMatchObject({ time: "2026-11-05", text: "매수", color: "red" });
    expect(m.series.createPriceLine).toHaveBeenCalledWith(expect.objectContaining({ price: 99, title: "손절", color: "blue" }));
  });

  test("ResizeObserver 로 폭을 맞추고, 언마운트하면 remove·disconnect", () => {
    const { unmount } = render(<Chart series={[{ time: "2026-11-04", value: 1 }, { time: "2026-11-05", value: 1.5 }]} />);
    roCallback!([{ contentRect: { width: 640 } } as ResizeObserverEntry], {} as ResizeObserver);
    expect(m.chart.applyOptions).toHaveBeenCalledWith({ width: 640 });
    unmount();
    expect(m.chart.remove).toHaveBeenCalledTimes(1);
    expect(disconnect).toHaveBeenCalled();
  });

  test("series 가 바뀌면 차트를 다시 만들지 않고 setData 만", () => {
    const r = render(<Chart series={[{ time: "2026-11-04", value: 1 }, { time: "2026-11-05", value: 1.5 }]} />);
    r.rerender(<Chart series={[{ time: "2026-11-04", value: 1 }, { time: "2026-11-05", value: 2 }]} />);
    expect(m.createChart).toHaveBeenCalledTimes(1);
    expect(m.series.setData).toHaveBeenLastCalledWith([{ time: "2026-11-04", value: 1 }, { time: "2026-11-05", value: 2 }]);
  });

  test("extra 는 선 시리즈를 더 붙여 setData 하고, 바뀌면 옛 시리즈를 뗀다", () => {
    const extra = [{ label: "1/N 균등", data: [{ time: "2026-11-05", value: 1.01 }] }];
    const r = render(<Chart series={[{ time: "2026-11-04", value: 1 }, { time: "2026-11-05", value: 1.5 }]} extra={extra} />);
    expect(m.chart.addSeries).toHaveBeenCalledTimes(2);
    expect((m.chart.addSeries.mock.calls[1] as unknown[])[1]).toMatchObject({ title: "1/N 균등" });
    expect(m.series.setData).toHaveBeenCalledWith([{ time: "2026-11-05", value: 1.01 }]);
    r.rerender(<Chart series={[{ time: "2026-11-04", value: 1 }, { time: "2026-11-05", value: 1.5 }]} extra={[]} />);
    expect(m.chart.removeSeries).toHaveBeenCalledTimes(1);
  });

  test("candles 를 주면 CandlestickSeries 로 그리고 표식·가격선은 그 시리즈에", () => {
    const candles = [{ time: "2026-11-05", open: 100, high: 110, low: 95, close: 105 }];
    render(
      <Chart
        candles={candles}
        markers={[{ time: "2026-11-05", text: "매수", color: "red" }]}
        lines={[{ price: 99, label: "손절", color: "blue" }]}
      />,
    );
    expect(m.chart.addSeries).toHaveBeenCalledTimes(1);
    expect((m.chart.addSeries.mock.calls[0] as unknown[])[0]).toBe(m.CandlestickSeries);
    expect(m.series.setData).toHaveBeenLastCalledWith(candles);
    expect((m.createSeriesMarkers.mock.calls[0] as unknown[])[0]).toBe(m.series);
    expect(m.series.createPriceLine).toHaveBeenCalledWith(expect.objectContaining({ price: 99, title: "손절" }));
  });
});

describe("빈 자료", () => {
  test("점이 없으면 차트를 만들지 않고 같은 높이의 빈 틀에 「자료 없음」을 쓴다", () => {
    const { container } = render(<Chart series={[]} height={200} />);
    expect(m.createChart).not.toHaveBeenCalled();
    const empty = container.querySelector("[data-chart-empty]") as HTMLElement;
    expect(empty).not.toBeNull();
    expect(empty.textContent).toContain("자료 없음");
    expect(empty.style.height).toBe("200px");
  });

  test("캔들이 빈 배열이어도 같다", () => {
    const { container } = render(<Chart candles={[]} />);
    expect(m.createChart).not.toHaveBeenCalled();
    expect(container.querySelector("[data-chart-empty]")).not.toBeNull();
  });
});

describe("점 1개", () => {
  test("선 시리즈 점이 1개면 차트를 만들지 않고 「자료 1일 — 추이는 내일부터」", () => {
    const { container } = render(<Chart series={[{ time: "2026-11-05", value: 1 }]} />);
    expect(container.querySelector("[data-chart-empty]")?.textContent).toBe("자료 1일 — 추이는 내일부터");
    expect(m.createChart).not.toHaveBeenCalled();
  });

  test("선 시리즈 값이 전부 같으면(평평한 선) 차트 대신 「자료 N일 — 변동 없음」", () => {
    const { container } = render(<Chart series={[{ time: "2026-11-04", value: 10000000 }, { time: "2026-11-05", value: 10000000 }]} />);
    expect(container.querySelector("[data-chart-empty]")?.textContent).toBe("자료 2일 — 변동 없음 (10,000,000)");
    expect(m.createChart).not.toHaveBeenCalled();
  });
});

describe("축 축약·1점 가드(벤치마크 있어도)", () => {
  test("선 시리즈 점이 1개면 벤치마크 선이 있어도 「자료 1일」 빈 틀", () => {
    const { container } = render(<Chart series={[{ time: "2026-11-05", value: 1 }]} extra={[{ label: "1/N", data: [{ time: "2026-11-05", value: 1 }] }]} />);
    expect(container.querySelector("[data-chart-empty]")!.textContent).toContain("자료 1일");
    expect(m.createChart).not.toHaveBeenCalled();
  });

  test("가격축은 큰 금액을 「1,235만」「1.5억」 으로 줄이고 작은 값은 그대로", () => {
    render(<Chart series={[{ time: "2026-11-04", value: 10_000_000 }, { time: "2026-11-05", value: 10_500_000 }]} />);
    const opts = (m.createChart.mock.calls[0] as unknown[])[1] as { localization: { priceFormatter: (v: number) => string } };
    const f = opts.localization.priceFormatter;
    expect(f(12_345_678)).toBe("1,235만");
    expect(f(10_000_000)).toBe("1,000만");
    expect(f(150_000_000)).toBe("1.5억");
    expect(f(70_500)).toBe("70,500");
    expect(f(1.0523)).toBe("1.05");
    expect(f(-2_500_000)).toBe("-250만");
  });
});
