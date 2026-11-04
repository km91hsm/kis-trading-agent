import { useEffect, useState } from "react";
import { describe, expect, test } from "vitest";
import { installFakeFetch } from "../fakeFetch";
import { flush, render } from "../render";

describe("render", () => {
  test("unmount 를 잊은 테스트(다음 테스트에서 정리 확인)", () => {
    const { container } = render(<p>남김</p>);
    expect(container.textContent).toBe("남김");
    expect(document.body.childElementCount).toBe(1);
  });
  test("앞 테스트가 남긴 트리는 afterEach 가 떼어 냈다", () => {
    expect(document.body.childElementCount).toBe(0);
  });
  test("rerender 는 같은 컨테이너를 갱신하고 unmount 는 떼어 낸다", () => {
    const r = render(<p>하나</p>);
    r.rerender(<p>둘</p>);
    expect(r.container.textContent).toBe("둘");
    r.unmount();
    expect(document.body.childElementCount).toBe(0);
  });
});

describe("flush", () => {
  test("대기 중인 promise 뒤의 상태 갱신을 반영한다", async () => {
    function C() {
      const [v, setV] = useState("대기");
      useEffect(() => {
        Promise.resolve().then(() => setV("완료"));
      }, []);
      return <p>{v}</p>;
    }
    const { container } = render(<C />);
    await flush();
    expect(container.textContent).toBe("완료");
  });
});

describe("flush + installFakeFetch", () => {
  test("가짜 fetch 응답의 json() 까지 기다려 화면에 반영한다", async () => {
    const f = installFakeFetch({ "GET /api/z": { v: "받음" } });
    try {
      function C() {
        const [v, setV] = useState("대기");
        useEffect(() => {
          fetch("/api/z")
            .then((r) => r.json())
            .then((b: { v: string }) => setV(b.v));
        }, []);
        return <p>{v}</p>;
      }
      const { container } = render(<C />);
      await flush();
      expect(container.textContent).toBe("받음");
    } finally {
      f.restore();
    }
  });
});

describe("installFakeFetch", () => {
  test("쿼리는 매칭에서 무시하고 함수 핸들러에 URL 을 넘긴다", async () => {
    const f = installFakeFetch({ "GET /api/x": (u: URL) => ({ a: u.searchParams.get("a") }) });
    try {
      const r = await fetch("/api/x?a=1");
      expect(r.status).toBe(200);
      expect(await r.json()).toEqual({ a: "1" });
      expect(Number.isNaN(Date.parse(r.headers.get("X-Server-Time") ?? ""))).toBe(false);
      expect(f.calls).toEqual([{ url: "/api/x?a=1", init: undefined }]);
    } finally {
      f.restore();
    }
  });
  test("없는 경로는 404 {detail:'no route'}, 메서드도 키에 들어간다", async () => {
    const f = installFakeFetch({ "GET /api/x": {} });
    try {
      const r = await fetch("/api/x", { method: "POST" });
      expect(r.status).toBe(404);
      expect(await r.json()).toEqual({ detail: "no route" });
    } finally {
      f.restore();
    }
  });
  test("핸들러가 Response 를 돌려주면 그대로 통과시키고 호출은 기록한다", async () => {
    const f = installFakeFetch({
      "POST /api/y": () => new Response(JSON.stringify({ detail: "거부" }), { status: 409 }),
    });
    try {
      const r = await fetch("/api/y", { method: "POST" });
      expect(r.status).toBe(409);
      expect(await r.json()).toEqual({ detail: "거부" });
      expect(f.calls).toHaveLength(1);
    } finally {
      f.restore();
    }
  });
  test("restore 는 원래 fetch 를 되돌린다", () => {
    const original = globalThis.fetch;
    const f = installFakeFetch({});
    expect(globalThis.fetch).not.toBe(original);
    f.restore();
    expect(globalThis.fetch).toBe(original);
  });
});
