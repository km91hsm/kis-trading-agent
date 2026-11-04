// 테스트용 가짜 fetch — "<METHOD> <pathname>" 키로 응답을 고른다(쿼리 문자열은 매칭에서 무시).
// 값이 함수면 (URL, init) 으로 불러 그 반환값을 JSON 으로 돌려준다(반환값이 Response 면 그대로 — 4xx/5xx 흉내).
// 없는 경로는 404 {detail:"no route"}.

type Handler = unknown | ((url: URL, init?: RequestInit) => unknown);

export interface FakeFetch {
  calls: { url: string; init?: RequestInit }[];
  restore(): void;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "X-Server-Time": new Date().toISOString() },
  });
}

export function installFakeFetch(routes: Record<string, Handler>): FakeFetch {
  const original = globalThis.fetch;
  const calls: FakeFetch["calls"] = [];
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
    calls.push({ url: raw, init });
    const url = new URL(raw, "http://localhost");
    const key = `${method} ${url.pathname}`;
    if (!(key in routes)) return json({ detail: "no route" }, 404);
    const h = routes[key];
    const body = typeof h === "function" ? await (h as (u: URL, i?: RequestInit) => unknown)(url, init) : h;
    return body instanceof Response ? body : json(body);
  };
  return {
    calls,
    restore() {
      globalThis.fetch = original;
    },
  };
}
