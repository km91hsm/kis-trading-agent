// 전체 자동매매 일시정지 표기 — GET /system/pause {paused, at, by}. 계좌 모드(자동/승인)와는 별개라는 점을 같이 알린다.
import { Link } from "react-router-dom";
import { fmtWhen } from "../lib/format";
import { useResource } from "../lib/hooks";

export type Pause = { paused: boolean; at: string | null; by: string | null };

const BY_LABEL: Record<string, string> = { tray: "▲ 메뉴", web: "웹", telegram: "텔레그램", cli: "명령줄" };
/** control_log 채널 → 사람이 읽는 경로. 모르는 값은 원문. */
export const pauseByLabel = (by: string | null | undefined): string => (by ? (BY_LABEL[by] ?? by) : "");

/** 일시정지 여부·시각·경로. 조회 실패·로딩 중에는 paused=false(배너 없음). */
export function usePause(): Pause {
  const r = useResource<Pause>("/system/pause", undefined, ["control", "accounts"]);
  return { paused: !!r.data?.paused, at: r.data?.at ?? null, by: r.data?.by ?? null };
}

/** 「<시각> <경로>」 — 없는 쪽은 생략. */
export function pauseWhen(p: Pause): string {
  return [p.at ? fmtWhen(p.at) : "", pauseByLabel(p.by)].filter(Boolean).join(" ");
}

export function PauseBanner({ pause }: { pause?: Pause }) {
  const own = usePause();
  const p = pause ?? own;
  if (!p.paused) return null;
  const when = pauseWhen(p);
  return (
    <p className="pause-banner" role="status">
      자동매매 전체 일시정지 중{when ? ` · ${when}` : ""} — 계좌 모드(자동/승인)는 그대로, 새 주문만 멈춤{" "}
      <Link to="/accounts">재개</Link>
    </p>
  );
}
