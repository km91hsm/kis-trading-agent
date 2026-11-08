import { act } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, test } from "vitest";
import { SavetickerOnboarding } from "../../app/shell/SavetickerOnboarding";
import { tokenStore } from "../../lib/api";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { flush, render } from "../../test/render";
import { SavetickerInstallCard } from "../system/SavetickerInstall";

const INSTALL = { registered: false, installed_version: null, bundled_version: "1.5.0", update_needed: false, package: "/data/saveticker-extension", extension_dir: "/data/saveticker-extension/extension" };
const status = (install: object = INSTALL, dismissed = false) => ({ configured: false, service: null, blocked: false, reason_text: null, last_success_at: null, health: null, install, onboarding: { dismissed } });
const RESULT = { ok: true, extension: "/data/saveticker-extension/extension", next_steps: ["a"], replaced: false };
let ff: FakeFetch;
beforeEach(() => { localStorage.clear(); sessionStorage.clear(); tokenStore.set("synthetic-token"); });
afterEach(() => { ff?.restore(); localStorage.clear(); sessionStorage.clear(); });
const button = (root: ParentNode, text: string) => Array.from(root.querySelectorAll("button")).find((b) => b.textContent === text) as HTMLButtonElement | undefined;
const click = async (el: HTMLElement | undefined) => { await act(async () => { el!.click(); }); await flush(); };
const posts = (path: string) => ff.calls.filter((c) => c.init?.method === "POST" && String(c.url).includes(path));
async function banner(get: unknown, post: unknown = RESULT) {
  ff = installFakeFetch({ "GET /api/system/saveticker": get, "POST /api/system/saveticker/install": post, "POST /api/system/saveticker/onboarding": { ok: true } });
  const out = render(<MemoryRouter><SavetickerOnboarding /></MemoryRouter>); await flush(); return out;
}
const ASK = "세이브티커 뉴스 수집을 쓰시겠어요?";

test("미등록이고 닫지 않았으면 첫 안내를 보이고, 등록됐거나 「안 씀」이면 숨긴다", async () => {
  expect((await banner(status())).container.textContent).toContain(ASK);
  ff.restore();
  expect((await banner(status({ ...INSTALL, registered: true }))).container.textContent).not.toContain(ASK);
  ff.restore();
  expect((await banner(status(INSTALL, true))).container.textContent).not.toContain(ASK);
});

test("「설치하기」는 설치 API 를 한 번 부르고 Chrome 로드 단계·경로 복사·설정 링크를 보인다", async () => {
  const { container } = await banner(status());
  await click(button(container, "설치하기"));
  expect(posts("/saveticker/install")).toHaveLength(1);
  expect(container.textContent).toContain("chrome://extensions");
  expect(container.textContent).toContain("/data/saveticker-extension/extension");
  expect(button(container, "경로 복사")).toBeTruthy();
  const link = container.querySelector('a[href="/system/admin#secrets"]');
  expect(link?.textContent).toContain("연결 키");
});

test("설치 실패는 오류 문구를 보이고 다시 누를 수 있다", async () => {
  const { container } = await banner(status(), () => new Response(JSON.stringify({ detail: "확장 팝업에서 수집을 중지한 뒤 다시 하세요" }), { status: 400, headers: { "content-type": "application/json" } }));
  await click(button(container, "설치하기"));
  expect(container.querySelector('[role="alert"]')?.textContent).toContain("수집을 중지한 뒤");
  expect(button(container, "설치하기")?.disabled).toBe(false);
});

test("「나중에」는 이번 세션만 닫고, 「안 씀」은 다시 묻지 않게 저장한다", async () => {
  const first = await banner(status());
  await click(button(first.container, "나중에"));
  expect(first.container.textContent).not.toContain(ASK);
  expect(sessionStorage.getItem("saveticker-onboarding-later")).toBe("1");
  ff.restore();
  expect((await banner(status())).container.textContent).not.toContain(ASK);
  sessionStorage.clear(); ff.restore();
  const second = await banner(status());
  await click(button(second.container, "안 씀"));
  const sent = posts("/saveticker/onboarding");
  expect(sent).toHaveLength(1);
  expect(JSON.parse(String(sent[0].init?.body))).toEqual({ dismissed: true });
  expect(second.container.textContent).not.toContain(ASK);
});

test("시스템 카드: 버전·최신 여부와 「설치/갱신」, 원격 화면에서는 설치 불가 안내", async () => {
  ff = installFakeFetch({ "POST /api/system/saveticker/install": RESULT });
  const local = render(<MemoryRouter><SavetickerInstallCard install={{ ...INSTALL, registered: true, installed_version: "1.5.0" }} /></MemoryRouter>); await flush();
  expect(local.container.textContent).toContain("확장 1.5.0 · 최신");
  expect(button(local.container, "설치/갱신")?.disabled).toBe(false);
  const remote = render(<MemoryRouter><SavetickerInstallCard install={{ ...INSTALL, package: null, extension_dir: null }} /></MemoryRouter>); await flush();
  expect(remote.container.textContent).toContain("이 PC 에서 직접 연 화면에서만 설치할 수 있습니다");
  expect(button(remote.container, "설치/갱신")?.disabled).toBe(true);
});
