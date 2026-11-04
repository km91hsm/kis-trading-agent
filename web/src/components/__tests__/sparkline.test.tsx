import { describe, expect, test } from "vitest";
import { Sparkline } from "../Sparkline";
import { render } from "../../test/render";

describe("Sparkline", () => {
  test("값마다 점 하나, 최댓값은 위·최솟값은 아래", () => {
    const { container } = render(<Sparkline values={[1, 3, 2]} width={100} height={20} />);
    const svg = container.querySelector("svg")!;
    expect(svg.getAttribute("viewBox")).toBe("0 0 100 20");
    const pts = container.querySelector("polyline")!.getAttribute("points")!.trim().split(/\s+/).map((p) => p.split(",").map(Number));
    expect(pts).toHaveLength(3);
    expect(pts[0][0]).toBe(0);
    expect(pts[2][0]).toBe(100);
    expect(pts[1][1]).toBeLessThan(pts[2][1]);
    expect(pts[2][1]).toBeLessThan(pts[0][1]);
  });
  test("값이 같으면 가운데 수평선, 비면 그리지 않는다", () => {
    const flat = render(<Sparkline values={[5, 5]} width={10} height={10} />);
    expect(flat.container.querySelector("polyline")!.getAttribute("points")).toBe("0,5 10,5");
    const empty = render(<Sparkline values={[]} />);
    expect(empty.container.querySelector("polyline")).toBeNull();
  });
});
