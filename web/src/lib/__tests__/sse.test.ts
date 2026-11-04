// 옛 `web/src/events.test.ts` 이식
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NOISY_KEYS, SSE_KEYS, type SseKey } from "../../generated/sse-keys";
import {
  canLead,
  changedKeys,
  createEventsClient,
  HEARTBEAT_MS,
  LEADER_KEY,
  parseLeader,
  POLL_FALLBACK_MS,
  significantKeys,
  STALE_MS,
  type EventSnapshot,
  type EventsDeps,
} from "../sse";

const snap = (over: Partial<Record<SseKey, string>> = {}): EventSnapshot =>
  ({ ...Object.fromEntries(SSE_KEYS.map((k) => [k, "0"])), ...over }) as EventSnapshot;

class FakeSource {
  static instances: FakeSource[] = [];
  url: string;
  readyState = 0;
  closed = false;
  onopen: ((e: Event) => void) | null = null;
  onerror: ((e: Event) => void) | null = null;
  listeners = new Map<string, ((e: MessageEvent) => void)[]>();
  constructor(url: string) {
    this.url = url;
    FakeSource.instances.push(this);
  }
  addEventListener(type: string, fn: (e: MessageEvent) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]);
  }
  removeEventListener() {}
  close() {
    this.closed = true;
    this.readyState = 2;
  }
  open() {
    this.readyState = 1;
    this.onopen?.(new Event("open"));
  }
  fail() {
    this.readyState = 0;
    this.onerror?.(new Event("error"));
  }
  emit(type: string, data: unknown) {
    for (const l of this.listeners.get(type) ?? []) l(new MessageEvent(type, { data: JSON.stringify(data) }));
  }
}

class FakeBus {
  channels: FakeChannel[] = [];
  make(): BroadcastChannel {
    const c = new FakeChannel(this);
    this.channels.push(c);
    return c as unknown as BroadcastChannel;
  }
}
class FakeChannel {
  listeners: ((e: { data: unknown }) => void)[] = [];
  closed = false;
  bus: FakeBus;
  constructor(bus: FakeBus) {
    this.bus = bus;
  }
  postMessage(msg: unknown) {
    for (const c of this.bus.channels) if (c !== this && !c.closed) for (const l of [...c.listeners]) l({ data: msg });
  }
  addEventListener(_t: "message", fn: (e: { data: unknown }) => void) {
    this.listeners.push(fn);
  }
  removeEventListener(_t: "message", fn: (e: { data: unknown }) => void) {
    this.listeners = this.listeners.filter((l) => l !== fn);
  }
  close() {
    this.closed = true;
  }
}

class FakeDoc extends EventTarget {
  visibilityState: DocumentVisibilityState = "visible";
  count = 0;
  addEventListener(...a: Parameters<EventTarget["addEventListener"]>) {
    this.count += 1;
    super.addEventListener(...a);
  }
  removeEventListener(...a: Parameters<EventTarget["removeEventListener"]>) {
    this.count -= 1;
    super.removeEventListener(...a);
  }
  show() {
    this.visibilityState = "visible";
    this.dispatchEvent(new Event("visibilitychange"));
  }
}

function memStorage(): Storage {
  const m = new Map<string, string>();
  return {
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
    removeItem: (k: string) => void m.delete(k),
    clear: () => m.clear(),
    key: () => null,
    get length() {
      return m.size;
    },
  };
}

function harness() {
  const storage = memStorage();
  const bus = new FakeBus();
  let t = 1_000_000;
  const docs: FakeDoc[] = [];
  const mk = (id: string, extra: Partial<EventsDeps> = {}) => {
    const doc = new FakeDoc();
    docs.push(doc);
    return createEventsClient({
      EventSourceCtor: FakeSource as unknown as typeof EventSource,
      channel: bus.make(),
      storage,
      now: () => t,
      document: doc as unknown as Document,
      selfId: id,
      onServerTime: () => {},
      ...extra,
    });
  };
  return { mk, storage, bus, docs, advance: (ms: number) => (t += ms) };
}

beforeEach(() => {
  FakeSource.instances = [];
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

describe("순수 함수", () => {
  it("리더 판정: 기록 없음·자기 것·오래된 하트비트만 리더 가능", () => {
    expect(canLead(null, "a", 0)).toBe(true);
    expect(canLead({ id: "a", ts: 0 }, "a", 100)).toBe(true);
    expect(canLead({ id: "b", ts: 0 }, "a", STALE_MS)).toBe(false);
    expect(canLead({ id: "b", ts: 0 }, "a", STALE_MS + 1)).toBe(true);
    expect(STALE_MS).toBe(6000);
    expect(parseLeader("{bad")).toBeNull();
    expect(parseLeader(null)).toBeNull();
    expect(parseLeader(JSON.stringify({ id: "x", ts: 5 }))).toEqual({ id: "x", ts: 5 });
  });

  it("changedKeys: 첫 스냅샷은 SSE 키 전부, 그 뒤엔 값이 바뀐 키만(server_time·모르는 키 제외)", () => {
    const a = snap();
    expect(changedKeys(null, a)).toEqual([...SSE_KEYS].sort());
    expect(changedKeys(a, { ...a, server_time: "x" })).toEqual([]);
    expect(changedKeys(a, { ...a, positions: "1", orders: "2" })).toEqual(["orders", "positions"]);
    expect(changedKeys(a, { ...a, bogus: "1" } as EventSnapshot)).toEqual([]);
    // daemon 은 실제로 객체({alive, minute}) — 값으로 비교한다
    const d1 = { ...a, daemon: { alive: true, minute: "m" } } as unknown as EventSnapshot;
    const d2 = { ...a, daemon: { alive: true, minute: "m" } } as unknown as EventSnapshot;
    expect(changedKeys(d1, d2)).toEqual([]);
  });

  it("significantKeys 는 NOISY_KEYS(daemon) 만 바뀐 스냅샷을 빈 배열로 만든다", () => {
    expect(NOISY_KEYS).toEqual(["daemon"]);
    const a = snap();
    expect(significantKeys(changedKeys(a, { ...a, daemon: "x" }))).toEqual([]);
    expect(significantKeys(["daemon", "positions"])).toEqual(["positions"]);
  });
});

describe("리더 탭·중계", () => {
  it("탭 둘이면 EventSource 는 하나만 /api/events 로 열리고 바뀐 키가 두 탭의 구독자에 간다", () => {
    const h = harness();
    const a = h.mk("a");
    const b = h.mk("b");
    const got: Record<string, SseKey[][]> = { a: [], b: [] };
    a.subscribe((k) => got.a.push(k));
    b.subscribe((k) => got.b.push(k));
    expect(FakeSource.instances.length).toBe(1);
    expect(FakeSource.instances[0].url).toBe("/api/events");
    const src = FakeSource.instances[0];
    src.open();
    src.emit("change", snap());
    src.emit("change", snap({ positions: "2" }));
    expect(got.a.at(-1)).toEqual(["positions"]);
    expect(got.b.at(-1)).toEqual(["positions"]);
    expect(a.status().connected).toBe(true);
    expect(b.status().connected).toBe(true);
    expect(parseLeader(h.storage.getItem(LEADER_KEY))?.id).toBe("a");
    a.close();
    b.close();
  });

  it("리더가 떠나면 다른 탭이 승계해 새 EventSource 를 연다", () => {
    const h = harness();
    const a = h.mk("a");
    const b = h.mk("b");
    a.close();
    expect(FakeSource.instances[0].closed).toBe(true);
    expect(FakeSource.instances.length).toBe(2);
    expect(parseLeader(h.storage.getItem(LEADER_KEY))?.id).toBe("b");
    b.close();
  });

  it("리더 하트비트가 멈추면 다음 틱에 승계한다", () => {
    const h = harness();
    const a = h.mk("a", { now: () => 1_000_000 }); // 멈춘 탭: 하트비트 시각이 그대로
    const b = h.mk("b");
    expect(FakeSource.instances.length).toBe(1);
    h.advance(STALE_MS + 1);
    vi.advanceTimersByTime(HEARTBEAT_MS);
    expect(FakeSource.instances.length).toBe(2);
    expect(parseLeader(h.storage.getItem(LEADER_KEY))?.id).toBe("b");
    a.close();
    b.close();
  });

  it("승계당한 옛 리더가 깨어나면 자기 EventSource 를 닫고 follower 로 내려간다", () => {
    const h = harness();
    let aNow = 1_000_000;
    const a = h.mk("a", { now: () => aNow }); // 숨겨져 타이머가 멈춘 리더
    const b = h.mk("b");
    h.advance(STALE_MS + 1);
    vi.advanceTimersByTime(HEARTBEAT_MS); // a 의 하트비트는 옛 시각 그대로 → b 가 승계
    expect(FakeSource.instances.length).toBe(2);
    expect(parseLeader(h.storage.getItem(LEADER_KEY))?.id).toBe("b");
    // a 가 깨어남: 기록은 b 것이고 신선하다 → 강등
    aNow = 1_000_000 + STALE_MS + 1 + HEARTBEAT_MS;
    vi.advanceTimersByTime(HEARTBEAT_MS);
    expect(FakeSource.instances[0].closed).toBe(true);
    expect(FakeSource.instances[1].closed).toBe(false);
    expect(FakeSource.instances.length).toBe(2);
    expect(parseLeader(h.storage.getItem(LEADER_KEY))?.id).toBe("b");
    // 강등된 a 는 b 의 중계를 받는다
    const got: SseKey[][] = [];
    a.subscribe((k) => got.push(k));
    FakeSource.instances[1].emit("change", snap({ orders: "9" }));
    expect(got.length).toBe(1);
    a.close();
    expect(FakeSource.instances[1].closed).toBe(false); // follower 의 close 는 리더를 건드리지 않는다
    b.close();
  });

  it("channel 이 null 이면 탭마다 직접 연다", () => {
    const c = createEventsClient({
      EventSourceCtor: FakeSource as unknown as typeof EventSource,
      channel: null,
      storage: memStorage(),
      document: new FakeDoc() as unknown as Document,
    });
    expect(FakeSource.instances.length).toBe(1);
    c.close();
    expect(FakeSource.instances[0].closed).toBe(true);
  });

  it("ping·change 의 server_time 을 onServerTime 에 넘긴다", () => {
    const seen: string[] = [];
    const h = harness();
    const a = h.mk("a", { onServerTime: (iso) => seen.push(iso) });
    const src = FakeSource.instances[0];
    src.emit("ping", { server_time: "2026-11-05T00:00:00+00:00" });
    src.emit("change", { ...snap(), server_time: "2026-11-05T00:00:01+00:00" });
    expect(seen).toEqual(["2026-11-05T00:00:00+00:00", "2026-11-05T00:00:01+00:00"]);
    a.close();
  });
});

describe("끊김·폴백·재연결", () => {
  it("끊긴 상태에서는 30초마다 전체 키를 흘린다", async () => {
    expect(POLL_FALLBACK_MS).toBe(30000);
    const h = harness();
    const a = h.mk("a");
    const got: SseKey[][] = [];
    a.subscribe((k) => got.push(k));
    const src = FakeSource.instances[0];
    src.open();
    await vi.advanceTimersByTimeAsync(POLL_FALLBACK_MS * 2);
    expect(got).toEqual([]); // 연결 중엔 폴백 없음
    src.fail();
    expect(a.status().connected).toBe(false);
    await vi.advanceTimersByTimeAsync(POLL_FALLBACK_MS - 1);
    expect(got).toEqual([]);
    await vi.advanceTimersByTimeAsync(1);
    expect(got).toEqual([[...SSE_KEYS]]);
    await vi.advanceTimersByTimeAsync(POLL_FALLBACK_MS);
    expect(got.length).toBe(2);
    FakeSource.instances.at(-1)!.open(); // 다시 열리면 멈춘다
    await vi.advanceTimersByTimeAsync(POLL_FALLBACK_MS * 2);
    expect(got.length).toBe(2);
    a.close();
  });

  it("visibilitychange(visible) 가 reconnect 를 부른다 — 끊긴 리더는 새 EventSource 를 연다", () => {
    const h = harness();
    const a = h.mk("a");
    const first = FakeSource.instances[0];
    first.open();
    h.docs[0].show(); // 연결 중이면 그대로
    expect(FakeSource.instances.length).toBe(1);
    first.fail();
    h.docs[0].show();
    expect(first.closed).toBe(true);
    expect(FakeSource.instances.length).toBe(2);
    a.close();
  });

  it("close() 뒤에는 타이머·리스너·채널·EventSource 가 남지 않는다", () => {
    const h = harness();
    const a = h.mk("a");
    const cb = vi.fn();
    a.subscribe(cb);
    FakeSource.instances[0].fail();
    expect(vi.getTimerCount()).toBeGreaterThan(0);
    a.close();
    expect(vi.getTimerCount()).toBe(0);
    expect(h.docs[0].count).toBe(0);
    expect((h.bus.channels[0] as unknown as FakeChannel).closed).toBe(true);
    expect((h.bus.channels[0] as unknown as FakeChannel).listeners.length).toBe(0);
    expect(FakeSource.instances[0].closed).toBe(true);
    vi.advanceTimersByTime(POLL_FALLBACK_MS * 3);
    expect(cb).not.toHaveBeenCalled();
  });

  it("subscribe 가 돌려준 해제 함수 뒤엔 콜백이 불리지 않는다", () => {
    const h = harness();
    const a = h.mk("a");
    const cb = vi.fn();
    const off = a.subscribe(cb);
    off();
    FakeSource.instances[0].emit("change", snap());
    expect(cb).not.toHaveBeenCalled();
    a.close();
  });
});
