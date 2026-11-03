"""scan 포트(`ports["scan"]`): 종목 → 오늘 상위 테마·shock 사건 라벨."""

from datetime import date
from types import SimpleNamespace

from trader.features.scan import ports
from trader.kernel.ledger import control

D = date(2026, 10, 26)


def test_theme_of_returns_today_top_themes_and_none_for_unknown(conn, settings):
    control.set_flag(
        conn,
        "theme_heat:KR",
        {
            "market": "KR",
            "date": D.isoformat(),
            "observed_at": "2026-10-26T02:00:00+00:00",
            "themes": [{"theme": "섹터:반도체", "rank": 1, "score": 0.9, "members": ["111111"]}],
        },
    )
    port = ports.build_port(SimpleNamespace(settings=settings))
    fn = port.theme_of(conn, "KR", D)
    assert fn("111111") == ["섹터:반도체"] and fn("999999") is None
    assert port.theme_of(conn, "KR", date(2026, 10, 27))("111111") is None  # 어제 기록
    cfg = settings.theme.model_copy(update={"enabled": False})
    off = ports.build_port(SimpleNamespace(settings=settings.model_copy(update={"theme": cfg})))
    assert off.theme_of(conn, "KR", D)("111111") is None


def test_theme_of_keeps_themes_when_events_config_fails(conn, settings, project, caplog):
    """사건 설정(events.yaml)이 깨져도 사건 쪽만 꺼지고 테마 소속은 그대로 답한다(옛 동작)."""
    control.set_flag(
        conn,
        "theme_heat:KR",
        {
            "market": "KR",
            "date": D.isoformat(),
            "observed_at": "2026-10-26T02:00:00+00:00",
            "themes": [{"theme": "섹터:반도체", "rank": 1, "score": 0.9, "members": ["111111"]}],
        },
    )

    (project / "config").mkdir(exist_ok=True)
    (project / "config" / "events.yaml").write_text(
        "enabled: true\nunknown_key: 1\n", encoding="utf-8"
    )  # extra=forbid 검증 실패
    port = ports.build_port(SimpleNamespace(settings=settings))
    with caplog.at_level("WARNING", logger="trader.scan"):
        fn = port.theme_of(conn, "KR", D)
    assert fn("111111") == ["섹터:반도체"] and fn("999999") is None
    assert "사건 설정 읽기 실패" in caplog.text


def test_sector_of_prefers_yaml_then_auto_cache(project, settings):
    """yaml 섹터가 자동 캐시(state/cache/sector_auto.json)보다 우선하고, 모르는 종목은 None."""
    import json

    (project / "config" / "sectors.yaml").write_text(
        'sectors: [반도체, 금융, 기타]\nkr:\n  "005930": 반도체\nus: {}\n', encoding="utf-8"
    )
    cache = project / "state" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "sector_auto.json").write_text(
        json.dumps({"KR": {"005930": {"sector": "금융"}, "105560": {"sector": "금융"}}}),
        encoding="utf-8",
    )
    fn = ports.build_port(SimpleNamespace(settings=settings)).sector_of("KR")
    assert fn("005930") == "반도체"  # yaml 우선
    assert fn("105560") == "금융"  # 자동 캐시
    assert fn("999999") is None


def test_watchlist_port_reads_scan_watchlist(conn, settings):
    from trader.features.scan import watchlist

    watchlist.set_watchlist(conn, ["005930", "000660"])
    port = ports.build_port(SimpleNamespace(settings=settings))
    assert port.watchlist(conn, "KR") == ["005930", "000660"]
