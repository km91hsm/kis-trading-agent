import { act } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test } from "vitest";
import { clearResourceCache } from "../../lib/cache";
import { useResource } from "../../lib/hooks";
import { setEventsClient } from "../../lib/sse";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { flush, render } from "../../test/render";
import { body } from "../performance/ResourceView";
import { System } from "../system/System.tsx";
import { FoLegs } from "../system/FoLegs";
import { Performance } from "../performance/Performance.tsx";
import { fixturePerfAccounts, fixturePerformance } from "../../test/fixtures/performance";
import { fixtureRegime, fixtureScalp } from "../../test/fixtures/system";

let ff: FakeFetch;
beforeEach(() => { clearResourceCache(); setEventsClient({ subscribe: () => () => {}, status: () => ({ connected: true, since: 0 }), close() {} }); });
afterEach(() => ff?.restore());
function News() { const r = useResource<{ text: string }>("/news", undefined, []); return body(r, "뉴스", (data) => <p>{data.text}</p>); }

test("오류는 재조회·수집 점검 안내를 제공하고 재시도로 정상 내용을 복원한다", async () => {
  let reads = 0;
  ff = installFakeFetch({ "GET /api/news": () => ++reads === 1 ? new Response(JSON.stringify({ detail: "합성 일시 오류" }), { status: 500 }) : { text: "복구된 합성 뉴스" } });
  const ui = render(<MemoryRouter><News /></MemoryRouter>); await flush();
  expect(ui.container.querySelector('[role="alert"]')).not.toBeNull();
  expect(ui.container.querySelector('a[href="/system/admin#saveticker"]')).not.toBeNull();
  const retry = Array.from(ui.container.querySelectorAll("button")).find((b) => b.textContent === "다시 조회");
  expect(retry).not.toBeNull();
  await act(async () => retry!.click()); await flush();
  expect(ui.container.textContent).toContain("복구된 합성 뉴스");
  expect(ui.container.querySelector('[role="alert"]')).toBeNull();
});

test("파생 정책에는 조작과 계좌 상세 링크만 두고 주문 다리는 계좌 상세에서 확인한다", async () => {
  const account = { id: "paper-fo", env: "paper", mode: "approve", state: "idle", paused: false, triggers: [] };
  ff = installFakeFetch({ "GET /api/system/fo": { accounts: [account], context: null }, "GET /api/settings/fo": { accounts: [{ ...account, enabled: false, effective: false, source: "yaml", reason: "꺼짐" }], default_paper: false, product: "mini", vehicles: [], max_contracts: 1 }, "GET /api/system/regime": fixtureRegime(), "GET /api/system/scalp": fixtureScalp() });
  const ui = render(<MemoryRouter><System page="features" /></MemoryRouter>); await flush();
  const fo = ui.container.querySelector("#fo")!;
  expect(fo.querySelector(".fo-legs")).toBeNull();
  expect(fo.querySelector('a[href^="/accounts/"]')).not.toBeNull();
  expect(ui.container.querySelector('nav[aria-label="매매 정책 구분"] a[href$="#fo"]')).not.toBeNull();
});

test("계좌 안의 파생 주문 상세도 실패 후 재조회할 수 있다", async () => {
  let reads = 0;
  ff = installFakeFetch({ "GET /api/fo/legs": () => ++reads === 1 ? new Response(JSON.stringify({ detail: "합성 일시 오류" }), { status: 503 }) : [] });
  const ui = render(<FoLegs accounts={[{ id: "paper-fo" }]} />); await flush();
  const retry = Array.from(ui.container.querySelectorAll("button")).find((b) => b.textContent === "다시 조회");
  expect(retry).toBeDefined();
  await act(async () => retry!.click()); await flush();
  expect(ui.container.textContent).toContain("오늘 주문 진행 기록 없음");
});

test("성과의 계좌 목록 오류도 화면 안에서 재조회해 선택기를 복원한다", async () => {
  let reads = 0;
  ff = installFakeFetch({ "GET /api/accounts": () => ++reads === 1 ? new Response(JSON.stringify({ detail: "합성 계좌 조회 오류" }), { status: 503 }) : fixturePerfAccounts(), "GET /api/analytics/performance": fixturePerformance({ nav: [] }) });
  const ui = render(<MemoryRouter><Performance /></MemoryRouter>); await flush();
  const accountError = Array.from(ui.container.querySelectorAll(".empty")).find((node) => node.textContent?.includes("합성 계좌 조회 오류"));
  const retry = accountError?.querySelector("button");
  expect(retry).toBeDefined();
  await act(async () => retry!.click()); await flush();
  expect(ui.container.querySelector('select[aria-label="계좌"]')).not.toBeNull();
  expect(ui.container.textContent).not.toContain("합성 계좌 조회 오류");
});
