import { act } from "react";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { Pager } from "../Pager";
import { Table } from "../Table";
import { usePaged } from "../../lib/usePaged";
import { render } from "../../test/render";

const ROWS = Array.from({ length: 65 }, (_, i) => `r${i + 1}`);

let originalScroll: typeof Element.prototype.scrollIntoView;
const scrolled: HTMLElement[] = [];
beforeEach(() => {
  originalScroll = Element.prototype.scrollIntoView;
  scrolled.length = 0;
  Element.prototype.scrollIntoView = vi.fn(function (this: HTMLElement) { scrolled.push(this); });
});
afterEach(() => { Element.prototype.scrollIntoView = originalScroll; });

function TableList({ name }: { name: string }) {
  const paged = usePaged(ROWS.map((value) => ({ value })), 30, name);
  return <section data-list={name}><h2>{name}</h2>
    <Table columns={[{ key: "value", label: "내용", width: "100%" }]} rows={paged.rows} rowKey={(row) => row.value} />
    <Pager {...paged} />
  </section>;
}

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

function NativeTableList() {
  const paged = usePaged(ROWS, 30);
  return <section><h2>지표</h2><div className="table-scroll">
    <table><tbody>{paged.rows.map((value) => <tr key={value}><td>{value}</td></tr>)}</tbody></table>
    <Pager {...paged} />
  </div></section>;
}

function mount(rows: string[], url = "/x") {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <List rows={rows} />
    </MemoryRouter>,
  );
}

describe("Pager + usePaged", () => {
  test("일반 table을 쓰는 지표 목록도 필터가 아닌 새 표 시작으로 이동한다", () => {
    const { container } = render(<MemoryRouter><NativeTableList /></MemoryRouter>);
    act(() => (container.querySelector('[data-page="2"]') as HTMLButtonElement).click());
    const table = container.querySelector("table") as HTMLElement;
    expect(table.textContent).toContain("r31");
    expect(scrolled).toEqual([table]);
    expect(document.activeElement).toBe(table);
  });
  test("다른 목록은 그대로 두고 변경한 목록의 새 첫 행으로 스크롤과 포커스를 옮긴다", () => {
    const { container } = render(<MemoryRouter><TableList name="first" /><TableList name="second" /></MemoryRouter>);
    const second = container.querySelector('[data-list="second"]') as HTMLElement;
    expect(scrolled).toHaveLength(0);
    act(() => (second.querySelector('[data-page="2"]') as HTMLButtonElement).click());
    const target = second.querySelector(".table-block") as HTMLElement;
    expect(target.textContent).toContain("r31");
    expect(scrolled).toEqual([target]);
    expect(document.activeElement).toBe(target);
    expect(container.querySelector('[data-list="first"] .table-block')?.textContent).toContain("r1");
    act(() => (second.querySelector('[data-page="2"]') as HTMLButtonElement).click());
    expect(scrolled).toHaveLength(1);
    act(() => (second.querySelector('[data-prev]') as HTMLButtonElement).click());
    expect(scrolled).toHaveLength(2);
    expect(target.textContent).toContain("r1");
  });
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
