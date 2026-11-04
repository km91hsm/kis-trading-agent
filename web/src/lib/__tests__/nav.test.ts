import { expect, test } from "vitest";
import { NAV } from "../../generated/nav";
import { navGroups } from "../../app/shell/Nav";

test("navGroups 는 다섯 화면을 NAV 순서로, items 를 order 오름차순으로 준다", () => {
  const g = navGroups();
  expect(g.map((x) => x.screen)).toEqual(["today", "accounts", "performance", "data", "system"]);
  expect(g[0].items.map((i) => i.id)).toEqual(["approvals", "spot-lane", "scalp-lane", "fo-lane"]);
  expect(g.map((x) => x.path)).toEqual(["/", "/accounts", "/performance", "/data", "/system"]);
  for (const grp of g) {
    const orders = grp.items.map((i) => i.order);
    expect(orders).toEqual([...orders].sort((a, b) => a - b));
    expect(grp.label).toBe(NAV[grp.screen].label);
  }
});
