"""nav.ts 생성: 커널 고정 5 화면 + 기능 NavItem."""

from __future__ import annotations

import json

from trader.kernel.registry import Feature, NavItem
from trader.kernel.web import gen


def fixture_feature_with_nav() -> Feature:
    return Feature(
        id="fixture",
        tier="service",
        nav=(
            NavItem(screen="today", id="fo-lane", label="파생", order=30),
            NavItem(screen="today", id="spot-lane", label="현물", order=10),
            NavItem(screen="system", id="regime", label="국면", order=30),
        ),
    )


def _nav_obj(text: str) -> dict:
    body = text.split("export const NAV = ", 1)[1].rsplit(" as const;", 1)[0]
    return json.loads(body)


def test_nav_ts_contains_screens_and_feature_items():
    out = gen.nav_ts([fixture_feature_with_nav()])
    assert "export const NAV = " in out and out.endswith(" as const;\n")
    assert out.startswith("// 자동 생성 파일")
    assert '{"screen":"today","id":"fo-lane","label":"파생","order":30,"page":"today"}' in out
    nav = _nav_obj(out)
    assert list(nav) == ["today", "accounts", "performance", "data", "system"]
    assert nav["today"]["label"] == "오늘" and nav["today"]["path"] == "/"
    assert nav["data"] == {
        "label": "데이터", "path": "/data", "items": [],
        "pages": [{"id": "data", "label": "데이터", "path": "/data"}],
    }
    # 커널 today 절(결정·주문·체결·다음 할 일·알림)은 기능 레인 카드(10~30) 뒤 40~70
    assert [i["id"] for i in nav["today"]["items"]] == [
        "spot-lane", "fo-lane", "decisions", "orders-timeline", "next-jobs", "notices",
    ]  # fmt: skip
    labels = [i["label"] for i in nav["today"]["items"]][2:]
    assert labels == ["결정", "주문·체결", "다음 할 일", "알림"]
    # 기능 절(국면 30…파생 60)은 운영 절(헬스…사용량) 뒤, 관리 절(설정…조작 이력) 앞에 끼어든다
    assert [i["id"] for i in nav["system"]["items"]] == [
        *KERNEL_SYSTEM_IDS[:5], "regime", *KERNEL_SYSTEM_IDS[5:],
    ]


KERNEL_SYSTEM_IDS = [
    "health", "daemon", "jobs", "runs", "usage", "token", "secrets", "settings", "control-log",
]


def test_nav_ts_kernel_system_sections_order_and_without_features():
    """시스템 화면의 커널 절은 기능 없이도 목차에 있고, 「지금 볼 것(헬스·데몬·잡·실행·사용량)
    → 기능(30~60) → 관리(토큰·연결 키·설정·이력 — 권한부터)」 순이다."""
    nav = _nav_obj(gen.nav_ts([]))
    items = nav["system"]["items"]
    assert [i["id"] for i in items] == KERNEL_SYSTEM_IDS
    assert [i["label"] for i in items] == [
        "헬스", "데몬", "잡 큐", "실행 기록", "사용량", "쓰기 토큰", "연결 키", "설정", "조작 이력",
    ]
    assert all(i["order"] < 30 for i in items[:5])
    assert all(i["order"] > 60 for i in items[5:])
    assert all(i["screen"] == "system" for i in items)
    assert [i["id"] for i in nav["today"]["items"]] == [
        "decisions", "orders-timeline", "next-jobs", "notices",
    ]  # fmt: skip


def test_nav_ts_screens_constant():
    assert gen.SCREENS == (
        ("today", "오늘", "/"),
        ("accounts", "계좌·보유", "/accounts"),
        ("performance", "성과", "/performance"),
        ("data", "데이터", "/data"),
        ("system", "시스템", "/system"),
    )


SYSTEM_PAGES = [
    {"id": "status", "label": "상태", "path": "/system"},
    {"id": "jobs", "label": "잡", "path": "/system/jobs"},
    {"id": "features", "label": "기능", "path": "/system/features"},
    {"id": "admin", "label": "관리", "path": "/system/admin"},
]


def test_nav_ts_system_pages_group_items_into_four_pages():
    """시스템 화면은 절 13개를 페이지 4개(상태·잡·기능·관리, 각자 주소)로 묶는다.
    항목마다 `page` 가 붙고, 기능이 보탠 시스템 절(국면·저장소·단타·파생)은 「기능」 페이지로
    간다. 다른 화면은 페이지 1개(자기 자신)."""
    nav = _nav_obj(gen.nav_ts([fixture_feature_with_nav()]))
    assert nav["system"]["pages"] == SYSTEM_PAGES
    page_of = {i["id"]: i["page"] for i in nav["system"]["items"]}
    assert page_of == {
        "health": "status", "daemon": "status", "usage": "status",
        "jobs": "jobs", "runs": "jobs",
        "regime": "features",
        "settings": "admin", "secrets": "admin", "token": "admin", "control-log": "admin",
    }
    assert nav["accounts"]["pages"] == [
        {"id": "accounts", "label": "계좌·보유", "path": "/accounts"},
    ]
    assert {i["page"] for i in nav["accounts"]["items"]} == set()


TODAY_PAGES = [
    {"id": "today", "label": "현황", "path": "/"},
    {"id": "log", "label": "기록", "path": "/log"},
]


def test_nav_ts_today_pages_split_status_and_log():
    """오늘 화면은 페이지 2개 — 현황(`/`: 승인·레인·결정)과 기록(`/log`: 주문·체결·다음 할 일·알림).
    기능 레인 카드(spot-lane·fo-lane)는 현황으로 간다(표에 없는 절은 첫 페이지)."""
    nav = _nav_obj(gen.nav_ts([fixture_feature_with_nav()]))
    assert nav["today"]["pages"] == TODAY_PAGES
    page_of = {i["id"]: i["page"] for i in nav["today"]["items"]}
    assert page_of == {
        "spot-lane": "today", "fo-lane": "today", "decisions": "today",
        "orders-timeline": "log", "next-jobs": "log", "notices": "log",
    }
    assert gen.page_of("today", "scalp-lane") == "today"
    assert gen.page_of("today", "unknown-section") == "today"
    assert gen.page_of("system", "unknown-section") == "features"
    assert gen.page_of("accounts", "overview") == "accounts"
