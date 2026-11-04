// 옛 `web/src/hooks.ts` 동작 일부 + M11b 자원 훅
import { act, createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { flush, render } from "../../test/render";
import { SSE_KEYS, type SseKey } from "../../generated/sse-keys";
import { ApiError } from "../api";
import { noteServerTime } from "../clock";
import { clearResourceCache } from "../cache";
import { useFoQuotes, useNow, useQuotes, useResource, useVisible } from "../hooks";
import { setEventsClient, type EventSnapshot, type EventsClient } from "../sse";

let fake: FakeFetch | null = null;
let subs: Set<(keys: SseKey[], snap: EventSnapshot) => void>;
let vis: DocumentVisibilityState = "visible";

function emit(keys: SseKey[]) {
  act(() => {
    for (const cb of [...subs]) cb(keys, {} as EventSnapshot);
  });
}
/** 가짜 타이머를 act 안에서 진행(타이머가 부른 상태 갱신까지 비운다). */
async function tick(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}
function setVisibility(v: DocumentVisibilityState) {
  vis = v;
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  subs = new Set();
  const client: EventsClient = {
    subscribe(cb) {
      subs.add(cb);
      return () => void subs.delete(cb);
    },
    status: () => ({ connected: true, since: 0 }),
    close() {},
  };
  setEventsClient(client);
  clearResourceCache();
  vis = "visible";
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => vis });
  Object.defineProperty(document, "hidden", { configurable: true, get: () => vis === "hidden" });
});
afterEach(() => {
  fake?.restore();
  fake = null;
  setEventsClient(null);
  delete (document as unknown as Record<string, unknown>).visibilityState;
  delete (document as unknown as Record<string, unknown>).hidden;
  noteServerTime(new Date(0).toISOString(), 0);
  vi.useRealTimers();
});

type Pos = { n: number };
function Res(props: { keys: SseKey[]; params?: Record<string, string>; enabled?: boolean }) {
  const r = useResource<Pos>("/positions", props.params ?? { account: "a" }, props.keys, { enabled: props.enabled });
  return createElement(
    "span",
    null,
    `${r.data?.n ?? "-"}|${r.loading ? "L" : ""}|${r.error ? (r.error instanceof ApiError ? r.error.status : "?") : ""}`,
  );
}

describe("useResource", () => {
  it("useResource 는 구독 키 'positions' 변화에만 재조회한다", async () => {
    let n = 0;
    fake = installFakeFetch({ "GET /api/positions": () => ({ n: ++n }) });
    const { container } = render(createElement(Res, { keys: ["positions"] }));
    await flush();
    expect(fake.calls.length).toBe(1);
    expect(fake.calls[0].url).toBe("/api/positions?account=a");
    expect(container.textContent).toBe("1||");
    await tick(1500);
    emit(["orders", "daemon"]);
    await flush();
    expect(fake.calls.length).toBe(1);
    emit(["positions"]);
    await flush();
    expect(fake.calls.length).toBe(2);
    expect(container.textContent).toBe("2||");
    // 30초 폴백은 전체 키를 흘린다 → 재조회
    await tick(1500);
    emit([...SSE_KEYS]);
    await flush();
    expect(fake.calls.length).toBe(3);
  });

  it("같은 path+params 는 1초 안에 한 번만 부른다", async () => {
    fake = installFakeFetch({ "GET /api/positions": { n: 7 } });
    const { container } = render(
      createElement("div", null, createElement(Res, { keys: ["positions"] }), createElement(Res, { keys: ["positions"] })),
    );
    await flush();
    expect(fake.calls.length).toBe(1);
    expect(container.textContent).toBe("7||7||");
    emit(["positions"]); // 같은 이벤트로 두 컴포넌트가 재조회해도 진행 중 요청을 나눠 1회
    await flush();
    expect(fake.calls.length).toBe(2);
  });

  it("SSE 변화가 1초 안에 와도 재조회한다(reload 직후 포함)", async () => {
    let n = 0;
    fake = installFakeFetch({ "GET /api/positions": () => ({ n: ++n }) });
    let reload: () => void = () => {};
    function R() {
      const r = useResource<Pos>("/positions", { account: "z" }, ["positions"]);
      reload = r.reload;
      return createElement("span", null, String(r.data?.n ?? "-"));
    }
    const { container } = render(createElement(R));
    await flush();
    expect(container.textContent).toBe("1");
    act(() => reload());
    await flush();
    expect(container.textContent).toBe("2");
    emit(["positions"]); // reload 응답 직후(1초 안) 변화
    await flush();
    expect(fake.calls.length).toBe(3);
    expect(container.textContent).toBe("3");
  });

  it("enabled:false 면 부르지 않고, 오류는 ApiError 로 둔다", async () => {
    fake = installFakeFetch({ "GET /api/positions": () => new Response(JSON.stringify({ detail: "x" }), { status: 500 }) });
    const off = render(createElement(Res, { keys: ["positions"], enabled: false }));
    await flush();
    expect(fake.calls.length).toBe(0);
    expect(off.container.textContent).toBe("-||");
    const on = render(createElement(Res, { keys: ["positions"], params: { account: "b" } }));
    await flush();
    expect(on.container.textContent).toBe("-||500");
  });

  it("언마운트하면 진행 중 요청을 AbortSignal 로 끊고 구독을 푼다", async () => {
    let signal: AbortSignal | undefined;
    fake = installFakeFetch({
      "GET /api/positions": (_u: URL, init?: RequestInit) =>
        new Promise((_res, rej) => {
          signal = init?.signal ?? undefined;
          signal?.addEventListener("abort", () => rej(new DOMException("aborted", "AbortError")));
        }),
    });
    const r = render(createElement(Res, { keys: ["positions"] }));
    await flush();
    expect(subs.size).toBe(1);
    expect(signal?.aborted).toBe(false);
    r.unmount();
    await flush();
    expect(signal?.aborted).toBe(true);
    expect(subs.size).toBe(0);
  });

  it("가려진 탭은 키 변화에도 미뤘다가 보이면 한 번 읽는다", async () => {
    fake = installFakeFetch({ "GET /api/positions": { n: 1 } });
    render(createElement(Res, { keys: ["positions"] }));
    await flush();
    await tick(1500);
    setVisibility("hidden");
    emit(["positions"]);
    emit(["positions"]);
    await flush();
    expect(fake.calls.length).toBe(1);
    setVisibility("visible");
    await flush();
    expect(fake.calls.length).toBe(2);
  });
});

const quote = (price: string) => ({ price, change_pct: 0.01, at: "2026-11-05T01:00:00+00:00", stale: false, source: "ws" });

function Q(props: { tickers: string[] }) {
  const r = useQuotes("KR", props.tickers);
  return createElement("span", null, r ? `${Object.keys(r.quotes).length}:${r.next_poll_ms}` : "none");
}

describe("useQuotes", () => {
  const tickers = Array.from({ length: 25 }, (_, i) => String(i).padStart(6, "0"));

  it("20개씩 나눠 부르고 합치며, next_poll_ms 마다 다시 부르고 가려지면 멈췄다가 보이면 즉시 부른다", async () => {
    fake = installFakeFetch({
      "GET /api/quotes": (u: URL) => ({
        session: "open",
        at: "2026-11-05T01:00:00+00:00",
        quotes: Object.fromEntries((u.searchParams.get("tickers") ?? "").split(",").map((t) => [t, quote("1")])),
        source: "ws",
        stream: true,
        hub_yield: null,
        next_poll_ms: 1000,
      }),
    });
    const r = render(createElement(Q, { tickers }));
    await flush();
    expect(fake.calls.length).toBe(2);
    const sizes = fake.calls.map((c) => new URL(c.url, "http://x").searchParams.get("tickers")!.split(",").length);
    expect(sizes).toEqual([20, 5]);
    expect(new URL(fake.calls[0].url, "http://x").searchParams.get("market")).toBe("KR");
    expect(r.container.textContent).toBe("25:1000");
    await tick(1000);
    expect(fake.calls.length).toBe(4);
    setVisibility("hidden");
    await tick(10_000);
    expect(fake.calls.length).toBeLessThanOrEqual(6); // 이미 예약된 한 번까지만
    const frozen = fake.calls.length;
    await tick(60_000);
    expect(fake.calls.length).toBe(frozen);
    setVisibility("visible");
    await flush();
    expect(fake.calls.length).toBe(frozen + 2);
    r.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("종목이 없으면 부르지 않는다", async () => {
    fake = installFakeFetch({});
    const r = render(createElement(Q, { tickers: [] }));
    await flush();
    expect(fake.calls.length).toBe(0);
    expect(r.container.textContent).toBe("none");
  });
});

function Fo(props: { codes: string[] }) {
  const r = useFoQuotes(props.codes);
  return createElement("span", null, r ? `${Object.keys(r.quotes).join(",")}:${r.kospi200?.price ?? "-"}` : "none");
}

describe("useFoQuotes", () => {
  it("/quotes/fo?codes= 로 부르고 kospi200 을 준다", async () => {
    fake = installFakeFetch({
      "GET /api/quotes/fo": {
        session: "open",
        at: "x",
        quotes: { A01: { kind: "future", price: "1", change_pct: 0, bid: "1", ask: "1", at: "x", stale: false, source: "rest" } },
        kospi200: { price: "400", at: "x", source: "rest" },
        next_poll_ms: 2000,
      },
    });
    const r = render(createElement(Fo, { codes: ["A01"] }));
    await flush();
    expect(new URL(fake.calls[0].url, "http://x").searchParams.get("codes")).toBe("A01");
    expect(r.container.textContent).toBe("A01:400");
  });
});

describe("useNow·useVisible", () => {
  it("useNow 는 서버 보정 시각을 tickMs 마다 준다", async () => {
    noteServerTime(new Date(Date.now() + 60_000).toISOString());
    let seen: Date | null = null;
    function N() {
      seen = useNow(1000);
      return null;
    }
    render(createElement(N));
    const t0 = (seen as unknown as Date).getTime();
    expect(Math.abs(t0 - (Date.now() + 60_000))).toBeLessThan(50);
    await tick(1000);
    expect((seen as unknown as Date).getTime()).toBeGreaterThanOrEqual(t0 + 1000);
  });

  it("useVisible 은 visibilitychange 를 따른다", () => {
    function V() {
      return createElement("span", null, useVisible() ? "on" : "off");
    }
    const r = render(createElement(V));
    expect(r.container.textContent).toBe("on");
    setVisibility("hidden");
    expect(r.container.textContent).toBe("off");
  });
});
