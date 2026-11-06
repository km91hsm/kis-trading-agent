import { act } from "react";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import { describe, expect, test } from "vitest";
import { Pager } from "../Pager";
import { usePaged } from "../../lib/usePaged";
import { render } from "../../test/render";

const ROWS = Array.from({ length: 65 }, (_, i) => `r${i + 1}`);

function List({ rows }: { rows: string[] }) {
  const paged = usePaged(rows, 30);
  const [sp] = useSearchParams();
  return (
    <>
      <ul>
        {paged.rows.map((r) => (
          <li key={r}>{r}</li>
        ))}
      </ul>
      <span data-url-page>{sp.get("page") ?? ""}</span>
      <Pager {...paged} />
    </>
  );
}

function mount(rows: string[], url = "/x") {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <List rows={rows} />
    </MemoryRouter>,
  );
}

describe("Pager + usePaged", () => {
  test("30행씩 자르고 「‹ 1 2 3 ›」 를 그린다, 누르면 ?page=N 과 행이 바뀐다", () => {
    const { container } = mount(ROWS);
    const lis = () => Array.from(container.querySelectorAll("li")).map((e) => e.textContent);
    expect(lis()).toHaveLength(30);
    expect(lis()[0]).toBe("r1");
    const nav = container.querySelector("nav.pager") as HTMLElement;
    expect(nav.getAttribute("aria-label")).toBe("페이지");
    const nums = Array.from(nav.querySelectorAll("button[data-page]")).map((b) => b.textContent);
    expect(nums).toEqual(["1", "2", "3"]);
    expect(nav.querySelector('button[data-page="1"]')?.getAttribute("aria-current")).toBe("page");
    act(() => (nav.querySelector('button[data-page="2"]') as HTMLButtonElement).click());
    expect(container.querySelector("[data-url-page]")?.textContent).toBe("2");
    expect(lis()).toHaveLength(30);
    expect(lis()[0]).toBe("r31");
    act(() => (nav.querySelector("button[data-next]") as HTMLButtonElement).click());
    expect(lis()).toEqual(["r61", "r62", "r63", "r64", "r65"]);
    expect((nav.querySelector("button[data-next]") as HTMLButtonElement).disabled).toBe(true);
    expect(nav.textContent).toContain("61–65 / 65");
  });
  test("주소의 page 가 범위를 벗어나면 마지막 페이지로, 한 페이지면 Pager 는 없다", () => {
    const a = mount(ROWS, "/x?page=9");
    expect(a.container.querySelectorAll("li")).toHaveLength(5);
    a.unmount();
    const b = mount(ROWS.slice(0, 12));
    expect(b.container.querySelectorAll("li")).toHaveLength(12);
    expect(b.container.querySelector("nav.pager")).toBeNull();
  });
});
