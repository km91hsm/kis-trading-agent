// vitest(jsdom) 공통 준비 — jsdom 에 없는 브라우저 API 를 최소 스텁으로 채우고(있으면 그대로 둔다),
// 테스트마다 localStorage 를 비운다.
import { beforeEach } from "vitest";

// React 에 act 환경임을 알려 "not wrapped in act" 경고를 막는다.
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const g = globalThis as Record<string, unknown>;

if (typeof window !== "undefined" && typeof window.matchMedia !== "function") {
  window.matchMedia = (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => false,
    }) as MediaQueryList;
}

if (typeof g.BroadcastChannel !== "function") {
  g.BroadcastChannel = class {
    name: string;
    onmessage: ((e: MessageEvent) => void) | null = null;
    constructor(name: string) {
      this.name = name;
    }
    postMessage() {}
    close() {}
    addEventListener() {}
    removeEventListener() {}
  };
}

if (typeof g.EventSource !== "function") {
  g.EventSource = class {
    static readonly CONNECTING = 0;
    static readonly OPEN = 1;
    static readonly CLOSED = 2;
    url: string;
    readyState = 0;
    onopen: ((e: Event) => void) | null = null;
    onmessage: ((e: MessageEvent) => void) | null = null;
    onerror: ((e: Event) => void) | null = null;
    constructor(url: string) {
      this.url = url;
    }
    addEventListener() {}
    removeEventListener() {}
    close() {
      this.readyState = 2;
    }
  };
}

for (const name of ["ResizeObserver", "IntersectionObserver"]) {
  if (typeof g[name] !== "function") {
    g[name] = class {
      observe() {}
      unobserve() {}
      disconnect() {}
      takeRecords() {
        return [];
      }
    };
  }
}

beforeEach(() => {
  if (typeof localStorage !== "undefined") localStorage.clear();
});
