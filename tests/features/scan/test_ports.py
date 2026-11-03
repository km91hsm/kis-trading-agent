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
