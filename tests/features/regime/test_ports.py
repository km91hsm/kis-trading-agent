"""regime 포트(`ports["regime"]`): 헤지 비활성·플래그 없음의 기본값."""

from types import SimpleNamespace

from trader.features.regime import ports
from trader.features.regime.settings import HedgeCfg
from trader.kernel.ledger import control


def _port(settings, **hedge):
    s = settings.model_copy(update={"hedge": HedgeCfg(**hedge)}) if hedge else settings
    return ports.build_port(SimpleNamespace(settings=s))


def test_port_state_bull_when_hedge_disabled(conn, settings):
    control.set_flag(conn, "regime:KR", {"state": "bear", "count": 2})
    assert _port(settings).state(conn, "KR") == "bear"
    assert _port(settings).state(conn, "US") == "bull"  # 플래그 없음
    assert _port(settings, enabled=False).state(conn, "KR") == "bull"


def test_port_hedge_tickers_empty_when_disabled(settings):
    assert _port(settings).hedge_tickers("KR") == frozenset({"114800", "252670"})
    assert _port(settings, enabled=False).hedge_tickers("KR") == frozenset()


def test_port_killswitch_message_only_when_engaged_for_account(conn, settings):
    """`killswitch_message`: 꺼져 있거나 다른 계좌만 막혀 있으면 None, 켜진 계좌면 알림 문구."""
    from trader.features.regime import killswitch, messages

    port = _port(settings)
    assert port.killswitch_message(conn, "paper-main") is None
    killswitch.engage(conn, reason="합성 손실", source="auto", account_id="real-main")
    assert port.killswitch_message(conn, "paper-main") is None  # 다른 계좌만 범위
    assert port.killswitch_message(conn, "real-main") == messages.killswitch_on("합성 손실")
