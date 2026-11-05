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
    assert '{"screen":"today","id":"fo-lane","label":"파생","order":30}' in out
    nav = _nav_obj(out)
    assert list(nav) == ["today", "accounts", "performance", "data", "system"]
    assert nav["today"]["label"] == "오늘" and nav["today"]["path"] == "/"
    assert nav["data"] == {"label": "데이터", "path": "/data", "items": []}
    assert [i["id"] for i in nav["today"]["items"]] == ["spot-lane", "fo-lane"]
    # 기능 절(국면 30…파생 60)은 운영 절(헬스…사용량) 뒤, 관리 절(설정…조작 이력) 앞에 끼어든다
    assert [i["id"] for i in nav["system"]["items"]] == [
        *KERNEL_SYSTEM_IDS[:5], "regime", *KERNEL_SYSTEM_IDS[5:],
    ]


KERNEL_SYSTEM_IDS = [
    "health", "daemon", "jobs", "runs", "usage", "settings", "secrets", "token", "control-log",
]


def test_nav_ts_kernel_system_sections_order_and_without_features():
    """시스템 화면의 커널 절은 기능 없이도 목차에 있고, 「지금 볼 것(헬스·데몬·잡·실행·사용량)
    → 기능(30~60) → 관리(설정·연결 키·토큰·이력)」 순이다."""
    nav = _nav_obj(gen.nav_ts([]))
    items = nav["system"]["items"]
    assert [i["id"] for i in items] == KERNEL_SYSTEM_IDS
    assert [i["label"] for i in items] == [
        "헬스", "데몬", "잡 큐", "실행 기록", "사용량", "설정", "연결 키", "쓰기 토큰", "조작 이력",
    ]
    assert all(i["order"] < 30 for i in items[:5])
    assert all(i["order"] > 60 for i in items[5:])
    assert all(i["screen"] == "system" for i in items)
    assert nav["today"]["items"] == []


def test_nav_ts_screens_constant():
    assert gen.SCREENS == (
        ("today", "오늘", "/"),
        ("accounts", "계좌·보유", "/accounts"),
        ("performance", "성과", "/performance"),
        ("data", "데이터", "/data"),
        ("system", "시스템", "/system"),
    )
