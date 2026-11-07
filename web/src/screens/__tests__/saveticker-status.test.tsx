import { act } from "react";
import { describe, expect, test } from "vitest";
import { SavetickerStatus } from "../system/SavetickerStatus";
import type { CollectionState, SavetickerResp } from "../system/system";
import { render } from "../../test/render";

function fixture(state: CollectionState = "normal"): SavetickerResp {
  return {
    configured: true, service: { state: "collecting", reason: null, at: null },
    blocked: false, health: "healthy", reason_text: null, last_success_at: "2026-11-04T00:00:00Z",
    summary: { state, recorded: 1, normal: state === "normal" ? 1 : 0, waiting: 0, login_required: 0, stopped: 0, error: state === "error" ? 1 : 0, partial: state === "partial" ? 1 : 0, empty: 0, delayed: state === "delayed" ? 1 : 0, unknown: 0, note: null },
    recovery: { state: "waiting", text: "Chrome에서 로그인과 확장 연결을 확인하세요" },
    streams: [{ id: "news_all", label: "뉴스 · 전체", state, status: "ok", count: 2, last_attempt_at: "2026-11-04T00:01:00Z", last_response_at: "2026-11-04T00:01:00Z", last_success_at: "2026-11-04T00:01:00Z", last_change_at: "2026-11-04T00:00:00Z", change_state: "unchanged", error: "최근 수집 요청에 오류가 있습니다" }],
  };
}

describe("수집 상세 상태", () => {
  test("지난 달력과 활성 여부 미확정 항목을 현재 지연으로 표시하지 않는다", () => {
    const s = fixture();
    s.streams = [
      { ...s.streams![0], id: "calendar_2026-08", label: "캘린더 · 2026-08", state: "delayed", scope: "historical" },
      { ...s.streams![0], id: "calendar_2026-12", label: "캘린더 · 2026-12", state: "delayed", scope: "unconfirmed" },
    ];
    const { container } = render(<SavetickerStatus s={s} />);
    const table = container.querySelector("table");
    expect(table?.textContent).toContain("과거 기록");
    expect(table?.textContent).toContain("대상 미확정");
    expect(table?.textContent).not.toContain("지연");
  });
  test.each([["partial", "일부 수집"], ["delayed", "지연"]] as const)("프로세스가 healthy여도 %s는 정상색이 아니다", (state, text) => {
    const { container } = render(<SavetickerStatus s={fixture(state)} />);
    const chip = container.querySelector(".stats .chip");
    expect(chip?.textContent).toBe(text);
    expect(chip?.getAttribute("data-tone")).toBe("warn");
  });

  test("좁은 화면의 행 카드에서도 시도·응답·유효 자료와 오류·복구를 확인한다", () => {
    const width = window.innerWidth;
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 390 });
    try {
      const { container } = render(<SavetickerStatus s={fixture("error")} />);
      const card = container.querySelector(".row-cards");
      expect(card?.textContent).toContain("최근 시도");
      expect(card?.textContent).toContain("최근 응답");
      expect(card?.textContent).toContain("유효 자료");
      const expand = card?.querySelector('button[aria-expanded="false"]') as HTMLButtonElement;
      expect(expand).toBeTruthy();
      act(() => expand.click());
      expect(card?.textContent).toContain("최근 수집 요청에 오류가 있습니다");
      expect(card?.textContent).toContain("Chrome에서 로그인과 확장 연결을 확인하세요");
      expect(expand.getAttribute("aria-expanded")).toBe("true");
    } finally {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
    }
  });
});
