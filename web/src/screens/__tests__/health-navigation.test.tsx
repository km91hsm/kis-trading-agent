import { act } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, test } from "vitest";
import App from "../../app/App";
import { clearResourceCache } from "../../lib/cache";
import { setEventsClient } from "../../lib/sse";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { fixtureHealth, fixtureUsage } from "../../test/fixtures";
import { flush, render } from "../../test/render";

let fake: FakeFetch | null = null;
const originalScroll = Element.prototype.scrollIntoView;
let scrolled: Element[];
const Location = () => {
  const location = useLocation();
  return <output data-location>{location.pathname}{location.hash}</output>;
};

beforeEach(() => {
  clearResourceCache();
  scrolled = [];
  Element.prototype.scrollIntoView = function () { scrolled.push(this); };
  setEventsClient({ subscribe: () => () => {}, status: () => ({ connected: true, since: 0 }), close() {} });
});
afterEach(() => {
  fake?.restore();
  Element.prototype.scrollIntoView = originalScroll;
});

// 점검기가 반환하는 절 주소를 실제 앱 셸에서 눌러 페이지 전환과 같은 화면 내 이동을 함께 확인한다.
test.each([
  ["jobs", "jobs.errors", "/system#jobs", "/system/jobs#jobs", "jobs"],
  ["llm", "llm.errors", "/system#usage", "/system#usage", "usage"],
  ["jobs", "jobs.daemon", "/system#daemon", "/system#daemon", "daemon"],
  ["regime", "regime.killswitch", "/system#regime", "/system/features#regime", "regime"],
  ["orders", "orders.unknown", "/#orders-timeline", "/log#orders-timeline", "orders-timeline"],
  ["evaluation", "evaluation.calibration_review", "/performance#calibration", "/performance/analysis#calibration", "calibration"],
  ["quotes", "quotes.session_busy.paper", "/data#quotes", "/data#quotes", "quotes"],
])("운영 점검 %s의 %s 바로가기는 대상 화면과 절로 이동한다", async (area, key, link, destination, id) => {
  fake = installFakeFetch({
    "GET /api/system/health": fixtureHealth({ reasons: [{ area, key, link, level: "warn", text: "합성 점검 사유", count: 1 }] }),
    "GET /api/system/usage": fixtureUsage(),
    "POST /api/system/usage/ensure-fresh": { fresh: true },
  });
  const { container } = render(<MemoryRouter initialEntries={["/system"]}><App /><Location /></MemoryRouter>);
  await flush();
  const reasonLink = container.querySelector<HTMLAnchorElement>("#health table a")!;
  expect(reasonLink?.textContent).toBe("보기 ›");
  act(() => reasonLink.click());
  await flush();
  expect(container.querySelector("[data-location]")?.textContent).toBe(destination);
  const target = container.querySelector(`#${id}`)!;
  expect(target).not.toBeNull();
  expect(scrolled).toContain(target);
});

test("상세 화면이 없는 만료 승인은 오해를 부르는 바로가기를 표시하지 않는다", async () => {
  fake = installFakeFetch({
    "GET /api/system/health": fixtureHealth({ reasons: [{ area: "orders", key: "orders.approvals_expired", link: null, level: "info", text: "합성 만료 승인 이력", count: 1 }] }),
    "GET /api/system/usage": fixtureUsage(),
    "POST /api/system/usage/ensure-fresh": { fresh: true },
    "GET /api/approvals": { approvals: [] },
  });
  const { container } = render(<MemoryRouter initialEntries={["/system"]}><App /></MemoryRouter>);
  await flush();
  const row = container.querySelector("#health table tbody tr");
  expect(row?.textContent).toContain("합성 만료 승인 이력");
  expect(row?.querySelector("a")).toBeNull();
});
