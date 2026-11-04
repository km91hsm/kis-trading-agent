// 옛 `web/src/api.test.ts` 이식
import { afterEach, describe, expect, it } from "vitest";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import {
  ApiError,
  AuthError,
  csvUrl,
  deleteJson,
  getAuthed,
  getJson,
  onAuthError,
  patchJson,
  postJson,
  postPublic,
  tokenStore,
} from "../api";
import { noteServerTime, serverOffsetMs } from "../clock";

let fake: FakeFetch | null = null;
function install(routes: Record<string, unknown>): FakeFetch {
  fake = installFakeFetch(routes);
  return fake;
}
afterEach(() => {
  fake?.restore();
  fake = null;
  noteServerTime(new Date(0).toISOString(), 0); // 오프셋 0 으로 되돌림
});

const header = (init: RequestInit | undefined, name: string) => (init?.headers as Record<string, string> | undefined)?.[name];

describe("getJson", () => {
  it("자원 경로 앞에 /api 를 붙이고 쿼리를 단다", async () => {
    const f = install({ "GET /api/accounts": [{ id: "paper-1" }] });
    await expect(getJson("/accounts", { env: "paper" })).resolves.toEqual([{ id: "paper-1" }]);
    expect(f.calls[0].url).toBe("/api/accounts?env=paper");
    expect(header(f.calls[0].init, "Authorization")).toBeUndefined(); // 조회는 토큰 없음
  });

  it("4xx 는 detail 메시지의 ApiError", async () => {
    install({
      "GET /api/orders": () => new Response(JSON.stringify({ detail: "account_id 가 필요합니다" }), { status: 400 }),
    });
    const err = await getJson("/orders").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).not.toBeInstanceOf(AuthError);
    expect((err as ApiError).status).toBe(400);
    expect((err as ApiError).message).toBe("account_id 가 필요합니다");
  });

  it("detail 이 없으면 상태 줄", async () => {
    install({ "GET /api/x": () => new Response("boom", { status: 500, statusText: "Internal Server Error" }) });
    await expect(getJson("/x")).rejects.toThrow("500 Internal Server Error");
  });

  it("2xx 인데 JSON 이 아니면(SPA HTML 폴백) ApiError", async () => {
    install({ "GET /api/x": () => new Response("<!doctype html><html></html>", { status: 200 }) });
    const err = await getJson("/x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(200);
    expect((err as ApiError).message).toBe("응답이 JSON 이 아닙니다");
  });

  it("fetch 자체가 실패하면 ApiError(0, 서버에 연결할 수 없습니다)", async () => {
    install({
      "GET /api/x": () => {
        throw new TypeError("Failed to fetch");
      },
    });
    const err = await getJson("/x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(0);
    expect((err as ApiError).message).toBe("서버에 연결할 수 없습니다");
  });

  it("opts.signal 이 fetch 에 전달된다", async () => {
    const f = install({ "GET /api/accounts": [] });
    const ac = new AbortController();
    await getJson("/accounts", undefined, { signal: ac.signal });
    expect(f.calls[0].init?.signal).toBe(ac.signal);
  });

  it("응답의 X-Server-Time 이 clock 오프셋에 반영된다", async () => {
    install({
      "GET /api/system/health": () =>
        new Response("{}", { status: 200, headers: { "X-Server-Time": new Date(Date.now() + 5000).toISOString() } }),
    });
    await getJson("/system/health");
    expect(Math.abs(serverOffsetMs() - 5000)).toBeLessThan(500);
  });

  it("오류 응답의 X-Server-Time 도 반영된다", async () => {
    install({
      "GET /api/x": () =>
        new Response("{}", { status: 404, headers: { "X-Server-Time": new Date(Date.now() - 3000).toISOString() } }),
    });
    await expect(getJson("/x")).rejects.toBeInstanceOf(ApiError);
    expect(Math.abs(serverOffsetMs() + 3000)).toBeLessThan(500);
  });
});

describe("쓰기", () => {
  it("쓰기는 토큰 없으면 요청 전에 AuthError", async () => {
    const f = install({ "PATCH /api/settings/paused": { ok: true } });
    tokenStore.set("");
    await expect(patchJson("/settings/paused", { value: true })).rejects.toBeInstanceOf(AuthError);
    expect(f.calls).toHaveLength(0);
  });

  it("postJson 은 Bearer 헤더와 JSON 본문을 보낸다", async () => {
    const f = install({ "POST /api/jobs": (_u: URL, init?: RequestInit) => ({ echo: JSON.parse(String(init?.body)) }) });
    tokenStore.set("T0KEN");
    await expect(postJson("/jobs", { kind: "scan" })).resolves.toEqual({ echo: { kind: "scan" } });
    expect(f.calls[0].url).toBe("/api/jobs");
    expect(f.calls[0].init?.method).toBe("POST");
    expect(header(f.calls[0].init, "Authorization")).toBe("Bearer T0KEN");
    expect(header(f.calls[0].init, "Content-Type")).toBe("application/json");
  });

  it("opts.token 이 저장된 토큰보다 우선한다", async () => {
    const f = install({ "POST /api/jobs": { ok: true } });
    tokenStore.set("OLD");
    await postJson("/jobs", null, { token: "NEW" });
    expect(header(f.calls[0].init, "Authorization")).toBe("Bearer NEW");
    expect(f.calls[0].init?.body).toBeUndefined();
  });

  it("401 과 403 모두 AuthError(상태 보존)", async () => {
    install({
      "PATCH /api/a": () => new Response(JSON.stringify({ detail: "토큰이 틀렸습니다" }), { status: 401 }),
      "POST /api/b": () => new Response(JSON.stringify({ detail: "이 PC 에서만" }), { status: 403 }),
    });
    tokenStore.set("T");
    const e401 = await patchJson("/a", {}).catch((e: unknown) => e);
    const e403 = await postJson("/b", {}).catch((e: unknown) => e);
    expect(e401).toBeInstanceOf(AuthError);
    expect((e401 as AuthError).status).toBe(401);
    expect((e401 as AuthError).message).toBe("토큰이 틀렸습니다");
    expect(e403).toBeInstanceOf(AuthError);
    expect((e403 as AuthError).status).toBe(403);
  });

  it("deleteJson 은 DELETE 로 보내고 빈 본문(204)을 받아들인다", async () => {
    const f = install({ "DELETE /api/jobs/7": () => new Response(null, { status: 204 }) });
    tokenStore.set("T");
    await expect(deleteJson("/jobs/7")).resolves.toBeUndefined();
    expect(f.calls[0].init?.method).toBe("DELETE");
    expect(header(f.calls[0].init, "Authorization")).toBe("Bearer T");
  });
});

describe("onAuthError", () => {
  it("토큰 없음·401·403 마다 불리고, 해제하면 더는 불리지 않는다", async () => {
    install({
      "GET /api/secret": () => new Response("{}", { status: 403 }),
      "POST /api/x": () => new Response("{}", { status: 401 }),
    });
    const seen: number[] = [];
    const off = onAuthError((e) => seen.push(e.status));
    tokenStore.set("");
    await patchJson("/x", {}).catch(() => {});
    await getJson("/secret").catch(() => {});
    tokenStore.set("T");
    await postJson("/x", {}).catch(() => {});
    expect(seen).toEqual([401, 403, 401]);
    off();
    await postJson("/x", {}).catch(() => {});
    expect(seen).toHaveLength(3);
  });

  it("던지는 리스너가 있어도 AuthError 로 거부되고 다음 리스너가 불린다", async () => {
    install({ "POST /api/x": () => new Response("{}", { status: 401 }) });
    tokenStore.set("T");
    const seen: number[] = [];
    const off1 = onAuthError(() => {
      throw new Error("리스너 고장");
    });
    const off2 = onAuthError((e) => seen.push(e.status));
    await expect(postJson("/x", {})).rejects.toBeInstanceOf(AuthError);
    expect(seen).toEqual([401]);
    off1();
    off2();
  });

  it("다른 오류에는 불리지 않는다", async () => {
    install({ "GET /api/x": () => new Response("{}", { status: 500 }) });
    const seen: AuthError[] = [];
    const off = onAuthError((e) => seen.push(e));
    await getJson("/x").catch(() => {});
    off();
    expect(seen).toHaveLength(0);
  });
});

describe("csvUrl", () => {
  it("/api 접두 + 쿼리 + format=csv", () => {
    expect(csvUrl("/calls", { run_id: "12" })).toBe("/api/calls?run_id=12&format=csv");
    expect(csvUrl("/orders")).toBe("/api/orders?format=csv");
  });
});

describe("tokenStore", () => {
  it("localStorage trader.webToken 에 저장·조회, 빈 값은 지운다", () => {
    tokenStore.set("abc");
    expect(tokenStore.get()).toBe("abc");
    expect(localStorage.getItem("trader.webToken")).toBe("abc");
    tokenStore.set("");
    expect(tokenStore.get()).toBeNull();
    expect(localStorage.getItem("trader.webToken")).toBeNull();
  });
});

describe("취소", () => {
  it("이미 취소된 signal 로 getJson 을 부르면 ApiError 가 아닌 AbortError 로 거부된다", async () => {
    const ctrl = new AbortController();
    ctrl.abort();
    const orig = globalThis.fetch;
    // 실제 fetch 처럼 취소된 signal 이면 AbortError(DOMException 이 아닐 수도 있는 클래스)로 거부
    globalThis.fetch = (async (_i: RequestInfo | URL, init?: RequestInit) => {
      if (init?.signal?.aborted) throw Object.assign(new Error("This operation was aborted"), { name: "AbortError" });
      return new Response("{}");
    }) as typeof fetch;
    try {
      const err = await getJson("/accounts", undefined, { signal: ctrl.signal }).catch((e: unknown) => e);
      expect(err).not.toBeInstanceOf(ApiError);
      expect((err as { name?: string }).name).toBe("AbortError");
    } finally {
      globalThis.fetch = orig;
    }
  });
});

describe("postPublic", () => {
  it("토큰 없이 Authorization 헤더 없이 POST 하고 JSON 을 돌려준다", async () => {
    tokenStore.set("tok"); // 저장돼 있어도 붙이지 않는다
    const f = install({ "POST /api/system/usage/ensure-fresh": { fresh: true, age_s: 3 } });
    await expect(postPublic("/system/usage/ensure-fresh")).resolves.toEqual({ fresh: true, age_s: 3 });
    expect(f.calls[0].url).toBe("/api/system/usage/ensure-fresh");
    expect(f.calls[0].init?.method).toBe("POST");
    expect(header(f.calls[0].init, "Authorization")).toBeUndefined();
    tokenStore.set("");
    install({ "POST /api/x": (_u: URL, init?: RequestInit) => JSON.parse(String(init?.body)) as unknown });
    await expect(postPublic("/x", { a: 1 })).resolves.toEqual({ a: 1 });
  });
});

describe("quiet403", () => {
  it("getAuthed(..., {quiet403: true}) 403 은 리스너를 부르지 않고 AuthError(403) 로 거부", async () => {
    tokenStore.set("tok");
    install({ "GET /api/settings/secrets": () => new Response(JSON.stringify({ detail: "이 PC 에서만" }), { status: 403 }) });
    const seen: AuthError[] = [];
    const off = onAuthError((e) => seen.push(e));
    try {
      const err = await getAuthed("/settings/secrets", undefined, { quiet403: true }).catch((e: unknown) => e);
      expect(err).toBeInstanceOf(AuthError);
      expect((err as AuthError).status).toBe(403);
      expect((err as AuthError).message).toBe("이 PC 에서만");
      expect(seen).toHaveLength(0);
      const err2 = await postPublic("/system/bootstrap-token", undefined, { quiet403: true }).catch((e: unknown) => e);
      expect((err2 as AuthError).status).toBe(404); // 경로 없음 — 403 아닌 오류는 그대로
    } finally {
      off();
      tokenStore.set("");
    }
  });

  it("quiet403 이어도 401 은 리스너에 통지", async () => {
    tokenStore.set("tok");
    install({ "GET /api/settings/secrets": () => new Response(JSON.stringify({ detail: "토큰이 틀렸습니다" }), { status: 401 }) });
    const seen: AuthError[] = [];
    const off = onAuthError((e) => seen.push(e));
    try {
      const err = await getAuthed("/settings/secrets", undefined, { quiet403: true }).catch((e: unknown) => e);
      expect((err as AuthError).status).toBe(401);
      expect(seen.map((e) => e.status)).toEqual([401]);
    } finally {
      off();
      tokenStore.set("");
    }
  });
});
