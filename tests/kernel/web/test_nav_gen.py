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
    assert nav["accounts"] == {
        "label": "계좌·보유", "path": "/accounts", "items": [],
        "pages": [{"id": "accounts", "label": "계좌·보유", "path": "/accounts"}],
    }
    assert nav["data"]["items"] == [] and [p["id"] for p in nav["data"]["pages"]] == [
        "quotes", "news", "themes", "features",
    ]
    # 커널 today 절(결정·주문·체결·다음 할 일·알림)은 기능 레인 카드(10~30) 뒤 40~70
    assert [i["id"] for i in nav["today"]["items"]] == [
        "spot-lane", "fo-lane", "pipeline", "decisions", "risk-checks",
        "orders-timeline", "next-jobs", "notices",
    ]  # fmt: skip
    labels = [i["label"] for i in nav["today"]["items"]][2:]
    assert labels == ["아침 파이프라인", "결정", "리스크 게이트", "주문·체결", "다음 할 일", "알림"]
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
        "운영 점검", "자동매매 서비스", "작업 대기열", "실행 기록", "AI 사용량",
        "설정 변경 인증", "연결 키", "설정", "조작 이력",
    ]
    assert all(i["order"] < 30 for i in items[:5])
    assert all(i["order"] > 60 for i in items[5:])
    assert all(i["screen"] == "system" for i in items)
    assert [i["id"] for i in nav["today"]["items"]] == [
        "pipeline", "decisions", "risk-checks", "orders-timeline", "next-jobs", "notices",
    ]  # fmt: skip


def test_nav_ts_screens_constant():
    assert gen.SCREENS == (
        ("today", "오늘", "/"),
        ("accounts", "계좌·보유", "/accounts"),
        ("performance", "성과", "/performance"),
        ("data", "시장 데이터", "/data"),
        ("system", "설정·운영", "/system"),
    )


SYSTEM_PAGES = [
    {"id": "status", "label": "운영 상태", "path": "/system"},
    {"id": "jobs", "label": "자동 작업", "path": "/system/jobs"},
    {"id": "features", "label": "매매 정책", "path": "/system/features"},
    {"id": "admin", "label": "연결·설정", "path": "/system/admin"},
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
        "spot-lane": "today", "fo-lane": "today", "pipeline": "today", "decisions": "today",
        "risk-checks": "today",
        "orders-timeline": "log", "next-jobs": "log", "notices": "log",
    }
    assert gen.page_of("today", "scalp-lane") == "today"
    assert gen.page_of("today", "unknown-section") == "today"
    assert gen.page_of("system", "unknown-section") == "features"
    assert gen.page_of("accounts", "overview") == "accounts"


PERFORMANCE_PAGES = [
    {"id": "performance", "label": "성과", "path": "/performance"},
    {"id": "memory", "label": "복기", "path": "/performance/memory"},
    {"id": "analysis", "label": "고급 분석", "path": "/performance/analysis"},
]
DATA_PAGES = [
    {"id": "quotes", "label": "시세·차트", "path": "/data"},
    {"id": "news", "label": "뉴스·공시", "path": "/data/news"},
    {"id": "themes", "label": "테마·감시목록", "path": "/data/themes"},
    {"id": "features", "label": "지표·모델", "path": "/data/features"},
]


def test_nav_ts_performance_and_data_pages():
    """성과는 페이지 3개(성과: 개요·계좌 비교 / 복기 / 추가 분석: 보정·섀도·RL·파생 성적표),
    데이터는 4개(시세·차트 / 뉴스·공시 / 테마·감시목록 / 지표·모델 — 하위 메뉴마다 한 페이지)."""
    nav = _nav_obj(gen.nav_ts([]))
    assert nav["performance"]["pages"] == PERFORMANCE_PAGES
    assert nav["data"]["pages"] == DATA_PAGES
    assert gen.page_of("performance", "performance-overview") == "performance"
    assert gen.page_of("performance", "compare") == "performance"
    assert gen.page_of("performance", "memory") == "memory"
    assert gen.page_of("performance", "fo-scorecard") == "performance"
    for sid in ("calibration", "shadow", "rl"):
        assert gen.page_of("performance", sid) == "analysis"
    assert gen.page_of("performance", "unknown-section") == "performance"
    for sid, page in (("quotes", "quotes"), ("news", "news"), ("themes", "themes"),
                      ("watchlist", "themes"), ("features", "features")):
        assert gen.page_of("data", sid) == page
    assert gen.page_of("data", "unknown-section") == "quotes"


def test_feature_nav_items_cover_performance_and_data_pages():
    """실제 기능의 nav 로 만든 nav.ts — 성과 항목(개요·계좌 비교·복기·보정·섀도·RL·파생 성적표)과
    데이터 항목(시세·차트·뉴스·공시·테마·감시목록·지표·모델)이 다 있고 라벨은 한글."""
    from trader.kernel.registry import discover

    nav = _nav_obj(gen.nav_ts(discover()))
    perf = {i["id"]: (i["label"], i["page"]) for i in nav["performance"]["items"]}
    assert perf == {
        "performance-overview": ("성과", "performance"), "compare": ("계좌 비교", "performance"),
        "memory": ("복기", "memory"),
        "calibration": ("판정 보정", "analysis"), "shadow": ("기준선 비교", "analysis"),
        "rl": ("강화학습 분석", "analysis"), "fo-scorecard": ("파생 성적표", "performance"),
    }
    data = {i["id"]: (i["label"], i["page"]) for i in nav["data"]["items"]}
    assert data == {
        "quotes": ("시세·차트", "quotes"), "news": ("뉴스·공시", "news"),
        "themes": ("테마", "themes"), "watchlist": ("감시목록", "themes"),
        "features": ("지표·모델", "features"),
    }


def test_collection_status_belongs_to_connection_settings_with_stable_id():
    """수집은 연결·설정에서 찾고 기존 절 id와 다른 매매 절 주소는 보존한다."""
    from trader.kernel.registry import discover

    nav = _nav_obj(gen.nav_ts(discover()))
    items = {item["id"]: item for item in nav["system"]["items"]}
    assert items["saveticker"]["page"] == "admin"
    assert items["regime"]["page"] == "features"
    assert items["regime"]["label"] == "시장 국면·매수 정책"
    assert [page["path"] for page in nav["system"]["pages"]] == [
        "/system", "/system/jobs", "/system/features", "/system/admin",
    ]
