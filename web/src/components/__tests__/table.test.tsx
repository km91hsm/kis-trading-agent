import { act } from "react";
import { afterEach, describe, expect, test, vi } from "vitest";
import { Chips } from "../Chip";
import { Table, type Column } from "../Table";
import { render } from "../../test/render";

type Row = { id: string; name: string; qty: number; side: string; memo: string };
const rows: Row[] = [
  { id: "a", name: "삼성전자", qty: 10, side: "매수", memo: "아주 긴 메모 ".repeat(10) },
  { id: "b", name: "SK하이닉스", qty: 3, side: "매도", memo: "짧은 메모" },
];
const columns: Column<Row>[] = [
  { key: "name", label: "종목", width: "8rem" },
  { key: "side", label: "구분", width: "4rem", align: "chip" },
  { key: "qty", label: "수량", width: "5rem", align: "num" },
  { key: "memo", label: "메모", width: "16rem", long: true },
];

const original = window.innerWidth;
function setWidth(w: number) {
  act(() => {
    Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: w });
    window.dispatchEvent(new Event("resize"));
  });
}
afterEach(() => {
  Object.defineProperty(window, "innerWidth", { configurable: true, writable: true, value: original });
});

describe("Table", () => {
  test("1024px 미만에서는 RowCard 로, 이상에서는 table 로 그린다", () => {
    setWidth(1280);
    const { container } = render(<Table columns={columns} rows={rows} rowKey={(r) => r.id} />);
    const table = container.querySelector("table")!;
    expect(table).not.toBeNull();
    expect(table.querySelectorAll("tbody tr")).toHaveLength(2);
    expect(Array.from(table.querySelectorAll("col")).map((c) => (c as HTMLElement).style.width)).toEqual(["8rem", "4rem", "5rem", "16rem"]);
    expect(table.querySelector("td.cell-num")!.textContent).toBe("10");
    expect(table.querySelector("td.cell-long")!.textContent).toContain("아주 긴 메모");

    setWidth(800);
    expect(container.querySelector("table")).toBeNull();
    const cards = container.querySelectorAll(".row-card");
    expect(cards).toHaveLength(2);
    expect(cards[0].querySelector(".row-card-meta")!.textContent).toContain("매수");
    expect(cards[0].querySelector(".row-card-title")!.textContent).toContain("아주 긴 메모");
    const chips = cards[0].querySelector(".row-card-chips")!.textContent!;
    expect(chips).toContain("삼성전자");
    expect(chips).toContain("10");

    setWidth(1024);
    expect(container.querySelector("table")).not.toBeNull();
  });

  test("언마운트하면 resize 구독을 푼다", () => {
    setWidth(1280);
    const add = vi.spyOn(window, "addEventListener");
    const remove = vi.spyOn(window, "removeEventListener");
    const { container, unmount } = render(<Table columns={columns} rows={rows} rowKey={(r) => r.id} />);
    expect(container.querySelector("table")).not.toBeNull();
    const handler = add.mock.calls.find((c) => c[0] === "resize")![1];
    unmount();
    expect(remove).toHaveBeenCalledWith("resize", handler);
    add.mockRestore();
    remove.mockRestore();
  });

  test("partial 이면 '일부 레인 응답 없음' 칩, csvHref 면 CSV 링크", () => {
    setWidth(1280);
    const { container } = render(
      <Table columns={columns} rows={rows} rowKey={(r) => r.id} partial csvHref="/api/x?format=csv" />,
    );
    expect(container.querySelector(".chip")!.textContent).toContain("일부 레인 응답 없음");
    const a = container.querySelector("a.table-csv") as HTMLAnchorElement;
    expect(a.textContent).toBe("CSV");
    expect(a.getAttribute("href")).toBe("/api/x?format=csv");
    expect(a.hasAttribute("download")).toBe(true);
  });

  test("long 열이 둘이면 첫 번째만 long(말줄임), 나머지는 text", () => {
    setWidth(1280);
    const cols: Column<Row>[] = [...columns, { key: "id", label: "id", width: "6rem", long: true }];
    const { container } = render(<Table columns={cols} rows={rows} rowKey={(r) => r.id} />);
    const tds = container.querySelectorAll("tbody tr:first-child td");
    expect(tds[3].classList.contains("cell-long")).toBe(true);
    expect(tds[4].classList.contains("cell-long")).toBe(false);
    expect(tds[4].querySelector(".cell-ellipsis")).toBeNull();
    setWidth(600);
    expect(container.querySelector(".row-card-title")!.textContent).toContain("아주 긴 메모");
    expect(container.querySelector(".row-card-chips")!.textContent).toContain("id a");
  });

  test("rows 가 비면 empty 문구", () => {
    setWidth(1280);
    const { container } = render(<Table columns={columns} rows={[]} rowKey={(r) => r.id} empty="주문이 없습니다" />);
    expect(container.querySelector("table")).toBeNull();
    expect(container.querySelector(".empty")!.textContent).toContain("주문이 없습니다");
  });

  test("expand 는 첫 칸 버튼으로 colSpan 행을 여닫는다(카드 모드는 카드 아래)", () => {
    setWidth(1280);
    const { container } = render(
      <Table columns={columns} rows={rows} rowKey={(r) => r.id} expand={(r) => <p className="detail">{r.name} 상세</p>} />,
    );
    expect(container.querySelector(".detail")).toBeNull();
    const btn = container.querySelector("tbody tr td button") as HTMLButtonElement;
    expect(btn.getAttribute("aria-expanded")).toBe("false");
    act(() => btn.click());
    const td = container.querySelector(".detail")!.closest("td")!;
    expect(td.colSpan).toBe(4);
    expect(btn.getAttribute("aria-expanded")).toBe("true");
    act(() => btn.click());
    expect(container.querySelector(".detail")).toBeNull();

    setWidth(600);
    const cardBtn = container.querySelector(".row-card button") as HTMLButtonElement;
    act(() => cardBtn.click());
    expect(container.querySelector(".row-card .detail")!.textContent).toBe("삼성전자 상세");
  });
});

describe("Chips", () => {
  test("Chips max=2 에 4개 → 2개 + '+2'", () => {
    const { container } = render(<Chips items={["가", "나", "다", "라"]} max={2} />);
    const chips = Array.from(container.querySelectorAll(".chip")).map((c) => c.textContent);
    expect(chips).toEqual(["가", "나", "+2"]);
  });
  test("max 이하면 +N 없음", () => {
    const { container } = render(<Chips items={["가", "나"]} max={2} />);
    expect(Array.from(container.querySelectorAll(".chip")).map((c) => c.textContent)).toEqual(["가", "나"]);
  });
});
