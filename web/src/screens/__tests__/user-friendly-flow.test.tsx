import { act } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, test } from "vitest";
import { DecisionsList } from "../today/DecisionsList";
import { Today } from "../today/Today.tsx";
import { SettingsPanel } from "../system/SettingsPanel";
import { SavetickerStatus } from "../system/SavetickerStatus";
import { controlRows } from "../system/system.ts";
import { fixtureSettings, fixtureSaveticker } from "../../test/fixtures/system";
import { laneCards } from "../today/today";
import { LaneCardView } from "../today/LaneCards";
import { PauseBanner } from "../../components/PauseNotice";
import { clearResourceCache } from "../../lib/cache";
import { setEventsClient } from "../../lib/sse";
import { fixtureAccount, fixtureSpotDecision, merged } from "../../test/fixtures/today";
import { installFakeFetch, type FakeFetch } from "../../test/fakeFetch";
import { flush, render } from "../../test/render";

let ff: FakeFetch | undefined;
beforeEach(() => {
  clearResourceCache();
  setEventsClient({ subscribe: () => () => {}, status: () => ({ connected: true, since: 0 }), close() {} });
});
afterEach(() => ff?.restore());
const location = () => { const l = useLocation(); return <output data-location>{l.search}</output>; };
const Location = location;

test("일반 종목 매수와 방어용 헤지를 구분하고 작업 집계를 매매 허용으로 표현하지 않는다", () => {
  const decisions = [fixtureSpotDecision(), fixtureSpotDecision({ id: 8, source: "hedge", ticker: "114800" }), fixtureSpotDecision({ id: 9, action: "HOLD", side: null })];
  const [card] = laneCards([fixtureAccount()], decisions, [], []);
  expect(card.decisions).toEqual({ buy: 1, sell: 0, hold: 1 });
  expect(card.hedgeDecisions).toBe(1);
  const { container } = render(<LaneCardView card={card} />);
  expect(container.textContent).toContain("방어용 헤지 1건");
  expect(container.textContent).toContain("현재 주문 대기 없음");
  expect(container.textContent).not.toContain("처리 완료");
});

test("판단 목록의 시장·종류 필터는 URL에 남고 헤지를 일반 매수와 구분한다", async () => {
  const items = [fixtureSpotDecision(), fixtureSpotDecision({ id: 8, source: "hedge", ticker: "114800", name: "합성 인버스" }), fixtureSpotDecision({ id: 9, market: "US", ticker: "SYN", name: "합성 미국 주식" })];
  const { container } = render(<MemoryRouter><DecisionsList items={items} partial={false} date="2026-11-10" /><Location /></MemoryRouter>);
  expect(container.textContent).toContain("방어용 헤지");
  const select = container.querySelector<HTMLSelectElement>('select[aria-label="판단 종류"]')!;
  expect(select).not.toBeNull();
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!.set!.call(select, "hedge");
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await flush();
  expect(container.querySelector("[data-location]")?.textContent).toContain("decision_kind=hedge");
  expect(container.querySelectorAll("tbody tr")).toHaveLength(1);
  expect(container.querySelector("tbody")?.textContent).toContain("114800");
  expect(container.querySelector("tbody")?.textContent).not.toContain("SYN");
});

test("일시정지는 판단 매도·승인 중지와 위험 청산 지속을 함께 설명한다", async () => {
  ff = installFakeFetch({ "GET /api/system/pause": { paused: true, at: null, by: null } });
  const { container } = render(<MemoryRouter><PauseBanner /></MemoryRouter>);
  await flush();
  expect(container.textContent).toContain("판단 매도·승인");
  expect(container.textContent).toContain("위험 청산은 계속");
  expect(container.textContent).not.toContain("진입만 멈춤");
});

test("오늘은 문제와 해결 링크를 상세 분석보다 먼저 보여준다", async () => {
  ff = installFakeFetch({
    "GET /api/approvals": { approvals: [] },
    "GET /api/accounts": [fixtureAccount()],
    "GET /api/decisions": merged([]), "GET /api/orders": merged([]),
    "GET /api/system/health": { level: "warn", checked_at: new Date().toISOString(), reasons: [{ area: "jobs", level: "warn", text: "합성 분석 오류 2건", link: "/system/jobs#runs", count: 2 }] },
  });
  const { container } = render(<MemoryRouter><Today /></MemoryRouter>);
  await flush();
  const attention = container.querySelector(".today-attention")!;
  expect(attention).not.toBeNull();
  expect(attention.textContent).toContain("합성 분석 오류 2건");
  expect(attention.querySelector('a[href="/system/jobs#runs"]')).not.toBeNull();
  expect(attention.compareDocumentPosition(container.querySelector("#pipeline")!)).toBe(4);
});

test("연결·설정에서 매매 조작 위치로 연결하고 수집 상세 기록은 접어둔다", async () => {
  ff = installFakeFetch({ "GET /api/settings": fixtureSettings() });
  const { container } = render(<MemoryRouter><SettingsPanel /><SavetickerStatus s={fixtureSaveticker()} /></MemoryRouter>);
  await flush();
  expect(container.querySelector('a[href="/system/features#scalp"]')).not.toBeNull();
  expect(container.querySelector('a[href="/system/features#fo"]')).not.toBeNull();
  const streams = container.querySelector<HTMLDetailsElement>("details[data-streams]");
  expect(streams).not.toBeNull();
  expect(streams!.open).toBe(false);
});

test("리스크 조작 이력은 내부 키나 JSON 대신 정책과 사유를 보여준다", () => {
  const [row] = controlRows([{ id: 1, at: "2026-11-10T01:00:00Z", channel: "web", actor: "사용자", key: "risk_policy:KR", old: null, new: { mode: "RISK_OFF", reason: "합성 방어 사유", expires_at: "2026-11-10T15:00:00Z" } }]);
  expect(row.key).toBe("국내 현물 매수 정책");
  expect(row.new).toContain("방어");
  expect(row.new).toContain("합성 방어 사유");
  expect(row.new).not.toContain('{"');
});
