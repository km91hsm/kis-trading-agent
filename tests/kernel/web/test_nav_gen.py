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
    assert [i["id"] for i in nav["system"]["items"]] == [*KERNEL_SYSTEM_IDS, "regime"]


KERNEL_SYSTEM_IDS = [
    "usage", "health", "daemon", "jobs", "runs", "settings", "secrets", "token", "control-log",
]


def test_nav_ts_kernel_system_sections_come_first_and_without_features():
    """시스템 화면의 커널 절(사용량…조작 이력)은 기능 없이도 목차에 있고,
    기능 항목(국면 30·저장소 40)보다 앞선다."""
    nav = _nav_obj(gen.nav_ts([]))
    items = nav["system"]["items"]
    assert [i["id"] for i in items] == KERNEL_SYSTEM_IDS
    assert [i["label"] for i in items] == [
        "사용량", "헬스", "데몬", "잡 큐", "실행 기록", "설정", "연결 키", "쓰기 토큰", "조작 이력",
    ]
    assert all(i["order"] < 30 for i in items)
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
