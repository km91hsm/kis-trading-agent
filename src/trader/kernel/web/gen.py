"""SSE 키·메뉴(nav)의 TypeScript 파일 생성 — 프런트가 손으로 적다 어긋나지 않게 한다."""

from __future__ import annotations

import json
from pathlib import Path

from trader.kernel.registry.types import Feature, NavItem
from trader.kernel.web.sse import KERNEL_NOISY_KEYS, KERNEL_SSE_KEYS

REL_PATH = Path("web/src/generated/sse-keys.ts")
REL_NAV_PATH = Path("web/src/generated/nav.ts")

# 커널 고정 화면 (id, 라벨, 경로). 기능은 NavItem 으로 이 아래 하위 목차만 보탠다
SCREENS = (
    ("today", "오늘", "/"),
    ("accounts", "계좌·보유", "/accounts"),
    ("performance", "성과", "/performance"),
    ("data", "데이터", "/data"),
    ("system", "시스템", "/system"),
)

# 시스템 화면의 커널 절(웹 `/system/*` 커널 라우트·ops).
# 기능 항목(국면 30·저장소 40·단타 50·파생 60)보다 앞에 선다.
# 시스템 절 순서: 지금 볼 것(0~) → 기능 절(30~60, 각 feature.py) → 관리(70~)
# 긴 화면은 절을 페이지 몇 개(각자 주소)로 묶는다 — (페이지 id, 라벨, 경로, 그 페이지의 절 id).
# 시스템은 절 13개를 상태·잡·기능·관리 넷으로, 오늘은 현황(승인·레인·결정)과
# 기록(주문·체결·다음 할 일·알림) 둘로. 표에 없는 화면은 페이지 1개(자기 자신).
# 절이 표에 없으면 절 목록이 빈 페이지(시스템 「기능」 — 기능이 보탠 국면·저장소·단타·파생 절이
# 여기로)로, 그런 페이지가 없으면 첫 페이지(오늘 현황)로 간다.
SYSTEM_PAGES = (
    ("status", "상태", "/system", ("health", "daemon", "usage")),
    ("jobs", "잡", "/system/jobs", ("jobs", "runs")),
    ("features", "기능", "/system/features", ()),
    ("admin", "관리", "/system/admin", ("token", "secrets", "settings", "control-log")),
)
TODAY_PAGES = (
    ("today", "현황", "/", ("approvals", "decisions")),
    ("log", "기록", "/log", ("orders-timeline", "next-jobs", "notices")),
)
SCREEN_PAGES: dict[str, tuple[tuple[str, str, str, tuple[str, ...]], ...]] = {
    "today": TODAY_PAGES,
    "system": SYSTEM_PAGES,
}


def page_of(screen: str, section_id: str) -> str:
    """절이 들어갈 페이지 id. 페이지 표가 없는 화면은 화면 id 자신."""
    pages = SCREEN_PAGES.get(screen)
    if not pages:
        return screen
    for pid, _, _, secs in pages:
        if section_id in secs:
            return pid
    for pid, _, _, secs in pages:
        if not secs:
            return pid
    return pages[0][0]

KERNEL_NAV = (
    # 오늘: 기능 레인 카드(10~30) 뒤에 커널 절
    NavItem("today", "decisions", "결정", 40),
    NavItem("today", "orders-timeline", "주문·체결", 50),
    NavItem("today", "next-jobs", "다음 할 일", 60),
    NavItem("today", "notices", "알림", 70),
    NavItem("system", "health", "헬스", 0),
    NavItem("system", "daemon", "데몬", 1),
    NavItem("system", "jobs", "잡 큐", 2),
    NavItem("system", "runs", "실행 기록", 3),
    NavItem("system", "usage", "사용량", 4),
    NavItem("system", "token", "쓰기 토큰", 70),
    NavItem("system", "secrets", "연결 키", 71),
    NavItem("system", "settings", "설정", 72),
    NavItem("system", "control-log", "조작 이력", 73),
)


def _ts_list(keys: list[str]) -> str:
    return "[" + ", ".join(f'"{k}"' for k in keys) + "]"


def sse_keys_ts(features: list[Feature]) -> str:
    """커널 키 + 기능 `sse_keys` 로 `sse-keys.ts` 본문을 만든다 (끝에 개행 한 줄)."""
    keys = list(KERNEL_SSE_KEYS)
    noisy = list(KERNEL_NOISY_KEYS)  # 데몬 하트비트는 잦게 바뀌는 키라 전역 갱신에서 뺀다
    for f in features:
        for k in f.sse_keys:
            keys.append(k.key)
            if k.noisy:
                noisy.append(k.key)
    return (
        "// 자동 생성 파일 — 수정 금지. `uv run trader dev gen-sse-keys` 로 다시 만든다.\n"
        f"export const SSE_KEYS = {_ts_list(keys)} as const;\n"
        "export type SseKey = (typeof SSE_KEYS)[number];\n"
        f"export const NOISY_KEYS: SseKey[] = {_ts_list(noisy)};\n"
    )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def nav_ts(features: list[Feature]) -> str:
    """커널 고정 5 화면(+ 페이지 표 `SCREEN_PAGES`) + 커널 절(`KERNEL_NAV`) + 기능 `nav` 로
    `nav.ts` 본문을 만든다(끝에 개행 한 줄).

    항목은 (screen, order, id) 순. 알 수 없는 screen 은 KeyError 로 생성이 실패한다."""
    rank = {sid: i for i, (sid, _, _) in enumerate(SCREENS)}
    items = sorted(
        (*KERNEL_NAV, *(n for f in features for n in f.nav)),
        key=lambda n: (rank[n.screen], n.order, n.id),
    )
    lines = []
    for sid, label, path in SCREENS:
        pages = (
            [{"id": pid, "label": pl, "path": pp} for pid, pl, pp, _ in SCREEN_PAGES[sid]]
            if sid in SCREEN_PAGES
            else [{"id": sid, "label": label, "path": path}]
        )
        mine = [
            {
                "screen": n.screen,
                "id": n.id,
                "label": n.label,
                "order": n.order,
                "page": page_of(sid, n.id),
            }
            for n in items
            if n.screen == sid
        ]
        head = (
            f'{_json(sid)}:{{"label":{_json(label)},"path":{_json(path)},'
            f'"pages":[{",".join(_json(p) for p in pages)}],"items":'
        )
        lines.append(f"  {head}[{','.join(_json(m) for m in mine)}]}}")
    return (
        "// 자동 생성 파일 — 수정 금지. `uv run trader dev gen-nav` 로 다시 만든다.\n"
        "export const NAV = {\n" + ",\n".join(lines) + "\n} as const;\n"
    )


def main() -> None:
    from trader.kernel.platform import paths
    from trader.kernel.registry import discover

    out = paths.project_root() / REL_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(sse_keys_ts(discover()), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
