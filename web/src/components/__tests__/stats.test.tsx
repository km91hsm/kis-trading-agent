import { describe, expect, test } from "vitest";
import { Stats } from "../Stats";
import { render } from "../../test/render";

describe("Stats", () => {
  test("라벨·값·부제를 dl.stats 격자로, 열 수와 크기는 data 속성으로", () => {
    const { container } = render(
      <Stats
        cols={3}
        size="kpi"
        items={[
          { label: "NAV", value: "10,000,000", sub: "22분 전" },
          { label: "상태", value: "동작 중", num: false },
        ]}
      />,
    );
    const dl = container.querySelector("dl.stats") as HTMLElement;
    expect(dl.getAttribute("data-cols")).toBe("3");
    expect(dl.getAttribute("data-size")).toBe("kpi");
    const stats = dl.querySelectorAll(".stat");
    expect(stats).toHaveLength(2);
    expect(stats[0].querySelector("dt")?.textContent).toBe("NAV");
    expect(stats[0].querySelector("dd.num")?.textContent).toBe("10,000,000");
    expect(stats[0].querySelector("dd.stat-sub")?.textContent).toBe("22분 전");
    expect(stats[1].querySelector("dd.num")).toBeNull();
    expect(stats[1].querySelector("dd")?.textContent).toBe("동작 중");
    expect(stats[1].querySelector("dd.stat-sub")).toBeNull();
  });

  test("기본은 cols auto · size sm, 값은 ReactNode 도 된다", () => {
    const { container } = render(<Stats items={[{ label: "단계", value: <span className="chip">정상</span>, num: false }]} />);
    const dl = container.querySelector("dl.stats") as HTMLElement;
    expect(dl.getAttribute("data-cols")).toBe("auto");
    expect(dl.getAttribute("data-size")).toBe("sm");
    expect(dl.querySelector("dd .chip")?.textContent).toBe("정상");
  });
});
