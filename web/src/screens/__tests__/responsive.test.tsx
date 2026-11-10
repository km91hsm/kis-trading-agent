// 반응형 점검(T14) — jsdom 은 레이아웃을 계산하지 않으므로 CSS 규칙 자체와 섹션 앵커를 고정한다.
// 표 ↔ 행 카드 전환은 CSS 미디어 쿼리가 아니라 `Table` 의 `useWide()`(1024px)가 한다 —
// 화면 고유 표(지표·호가)는 좁은 폭에서도 `.table-scroll` 가로 스크롤로 남으므로 CSS 로 `.table` 을 숨기지 않는다.
import { act } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, test } from "vitest";
import { ROUTES } from "../../app/router";
import { Table } from "../../components/Table";
import { NAV } from "../../generated/nav";
import { tokenStore } from "../../lib/api";
import { clearResourceCache } from "../../lib/cache";
import { setEventsClient, type EventsClient } from "../../lib/sse";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { flush, render } from "../../test/render";

// 스타일시트 원문(주석 제거). vitest 는 `css.include` 밖 CSS 를 빈 문자열로 바꾸므로(`?raw` 포함) 파일을 직접 읽는다.
// 프로젝트에 Node 타입이 없어 `node:fs` 는 타입 없이 동적 import 하고, 경로는 vitest 작업 폴더(web/) 기준.
type ReadFile = (path: string, enc: "utf-8") => string;
let readFile: ReadFile;
const cwd = () => (globalThis as unknown as { process: { cwd(): string } }).process.cwd();
beforeAll(async () => {
  const fs = (await import(/* @vite-ignore */ "node:" + "fs")) as { readFileSync: ReadFile };
  readFile = fs.readFileSync;
});
const css = (name: string) => readFile(`${cwd()}/src/styles/${name}`, "utf-8").replace(/\/\*[\s\S]*?\*\//g, "");
const ALL = ["tokens.css", "base.css", "shell.css", "components.css", "screens.css"];

/** `@media (...) { ... }` 블록 본문들(중첩 한 단계). */
function mediaBlocks(text: string, query: RegExp): string[] {
  const out: string[] = [];
  const re = /@media\s*([^{]+)\{/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (!query.test(m[1])) continue;
    let depth = 1;
    let i = re.lastIndex;
    for (; i < text.length && depth; i++) {
      if (text[i] === "{") depth++;
      else if (text[i] === "}") depth--;
    }
    out.push(text.slice(re.lastIndex, i - 1));
  }
  return out;
}
/** 선택자 목록에 target 이 있는 규칙 본문들. */
function rulesFor(text: string, target: RegExp): string[] {
  const out: string[] = [];
  const re = /([^{}]+)\{([^{}]*)\}/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m[1].split(",").some((s) => target.test(s.trim()))) out.push(m[2]);
  }
  return out;
}

let ff: FakeFetch | null = null;
const stub: EventsClient = { subscribe: () => () => {}, status: () => ({ connected: true, since: 0 }), close() {} };
beforeEach(() => {
  clearResourceCache();
  tokenStore.set("tok");
  setEventsClient(stub);
});
afterEach(() => {
  ff?.restore();
  ff = null;
});

function setWidth(w: number) {
  act(() => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: w });
    window.dispatchEvent(new Event("resize"));
  });
}

describe("반응형 규칙", () => {
  test("본문은 16px·보조 14px이며 모든 폭의 기본 조작 영역이 44px 이상이다", () => {
    const tokens = css("tokens.css");
    expect(tokens).toMatch(/--text-md:\s*16px/);
    expect(tokens).toMatch(/--text-sm:\s*14px/);
    const targets = [...tokens.matchAll(/--touch:\s*(\d+)px/g)].map((m) => Number(m[1]));
    expect(targets.length).toBeGreaterThan(0);
    expect(targets.every((v) => v >= 44)).toBe(true);
    expect(rulesFor(css("shell.css"), /^\.screen$/).join(";")).toMatch(/max-width:\s*1440px/);
    expect(rulesFor(css("screens.css"), /^\.page-tabs$/).join(";")).toMatch(/flex-wrap:\s*wrap/);
    expect(rulesFor(css("screens.css"), /^\.page-tabs a$/).join(";")).toMatch(/min-height:\s*var\(--touch\)/);
  });

  test("목록 이동 링크는 44px 영역이며 확인창은 작은 화면 안에서 끝까지 스크롤할 수 있다", () => {
    const comp = css("components.css");
    // 표·행 카드·운영 설명의 링크도 공통 규칙을 받아 클릭 영역이 글자 높이에 그치지 않는다.
    const link = rulesFor(css("base.css"), /^:where\(a\[href\]\)$/).join(";");
    expect(link).toMatch(/min-height:\s*var\(--touch\)/);
    expect(link).toMatch(/min-width:\s*var\(--touch\)/);
    expect(link).toMatch(/display:\s*inline-flex/);
    const dialog = rulesFor(comp, /^\.confirm$/).join(";");
    expect(dialog).toMatch(/max-height:[^;]*100dvh/);
    expect(dialog).toMatch(/overflow-y:\s*auto/);
    expect(dialog).toMatch(/overscroll-behavior:\s*contain/);
    const backdrop = rulesFor(comp, /^\.confirm-backdrop$/).join(";");
    expect(backdrop).toContain("safe-area-inset-top");
    expect(backdrop).toContain("safe-area-inset-bottom");
  });

  test("components.css 는 <1024px 에서 table 을 숨기고 행 카드를 쓰며, 전역 overflow-x 는 hidden 이 아니다(열 숨김 금지·가로 스크롤 허용)", () => {
    const comp = css("components.css");
    // 넘치는 표는 가로 스크롤(열을 숨기지 않는다), 행 카드는 좁은 폭에서도 줄어든다.
    expect(rulesFor(comp, /^\.table-scroll$/).join(";")).toMatch(/overflow-x:\s*auto/);
    expect(rulesFor(comp, /^\.row-cards$/).length).toBeGreaterThan(0);
    expect(rulesFor(comp, /^\.row-card$/).join(";")).toMatch(/min-width:\s*0/);
    // 어떤 좁은 폭 미디어 쿼리도 표 열(th/td/col)을 숨기지 않는다.
    for (const name of ALL) {
      for (const block of mediaBlocks(css(name), /max-width/)) {
        for (const body of rulesFor(block, /(^|\s|\.)(th|td|col)\b|\.cell-/)) {
          expect(body, `${name}: 좁은 폭에서 열 숨김`).not.toMatch(/display:\s*none/);
        }
      }
      // 전역 컨테이너는 가로 넘침을 감추지 않는다.
      for (const body of rulesFor(css(name), /^(html|body|:root|#root|\.main|\.shell|\.shell-main)$/)) {
        expect(body, `${name}: 전역 overflow-x hidden`).not.toMatch(/overflow(-x)?:\s*hidden/);
      }
    }
    // <1024 는 행 카드, ≥1024 는 table — 전환은 Table 의 useWide(1024).
    const cols = [{ key: "a", label: "A", width: "8rem" }];
    const rows = [{ a: "x" }];
    setWidth(390);
    const { container, unmount } = render(<Table columns={cols} rows={rows} rowKey={(r) => r.a} />);
    expect(container.querySelector(".row-cards .row-card")).not.toBeNull();
    expect(container.querySelector("table.table")).toBeNull();
    setWidth(1024);
    expect(container.querySelector("table.table")).not.toBeNull();
    expect(container.querySelector(".row-cards")).toBeNull();
    unmount();
  });

  test("모든 화면 섹션 id 가 NAV items id 를 전부 포함한다", async () => {
    setWidth(1440);
    ff = installFakeFetch({}); // 모든 API 404 — 섹션 컨테이너는 데이터 전에 그려진다.
    const groups = Object.values(NAV);
    const checked: string[] = [];
    for (const r of ROUTES) {
      // 화면의 페이지(시스템은 4개) 경로에 맞는 항목만 — 그 페이지가 그리는 절
      const group = groups.find((g) => g.pages.some((p) => p.path === r.path));
      const page = group?.pages.find((p) => p.path === r.path);
      const items = group && page ? group.items.filter((i) => i.page === page.id).map((i) => i.id) : [];
      const url = r.path.replace(":id", "x-1").replace(":asset", "spot");
      const { unmount } = render(
        <MemoryRouter initialEntries={[url]}>
          <Routes>
            <Route path={r.path} element={r.element} />
          </Routes>
        </MemoryRouter>,
      );
      await flush();
      for (const id of items) {
        expect(document.querySelector(`#${id}`), `${r.path} #${id}`).not.toBeNull(); // hidden 섹션도 있음으로 친다
        checked.push(id);
      }
      unmount();
    }
    const all = groups.flatMap((g) => g.items.map((i) => i.id));
    expect(checked.sort()).toEqual([...all].sort());
  });
});
