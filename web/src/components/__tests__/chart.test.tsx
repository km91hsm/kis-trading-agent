import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

const m = vi.hoisted(() => {
  const series = { setData: vi.fn(), createPriceLine: vi.fn(() => ({})), removePriceLine: vi.fn() };
  const markersApi = { setMarkers: vi.fn(), detach: vi.fn() };
  const chart = {
    addSeries: vi.fn(() => series),
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
  };
});
vi.mock("lightweight-charts", () => ({
  createChart: m.createChart,
  createSeriesMarkers: m.createSeriesMarkers,
  LineSeries: m.LineSeries,
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
    const { unmount } = render(<Chart series={[{ time: "2026-11-05", value: 1 }]} />);
    roCallback!([{ contentRect: { width: 640 } } as ResizeObserverEntry], {} as ResizeObserver);
    expect(m.chart.applyOptions).toHaveBeenCalledWith({ width: 640 });
    unmount();
    expect(m.chart.remove).toHaveBeenCalledTimes(1);
    expect(disconnect).toHaveBeenCalled();
  });

  test("series 가 바뀌면 차트를 다시 만들지 않고 setData 만", () => {
    const r = render(<Chart series={[{ time: "2026-11-05", value: 1 }]} />);
    r.rerender(<Chart series={[{ time: "2026-11-05", value: 2 }]} />);
    expect(m.createChart).toHaveBeenCalledTimes(1);
    expect(m.series.setData).toHaveBeenLastCalledWith([{ time: "2026-11-05", value: 2 }]);
  });
});
