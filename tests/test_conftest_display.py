"""실제 Chromium 테스트의 디스플레이 전원 skip 조건."""

import sys

from tests.conftest import display_is_off, display_off, display_power_state

IOREG_ON = (
    '      "IOPowerManagement" = {"CapabilityFlags"=32768,"MaxPowerState"=4,'
    '"DevicePowerState"=4,"CurrentPowerState"=4}'
)
IOREG_OFF = (
    '      "IOPowerManagement" = {"CapabilityFlags"=0,"MaxPowerState"=4,'
    '"ActivityTickles"=0,"DevicePowerState"=2,"IdleTimerPeriod"=300000,'
    '"CurrentPowerState"=2}'
)


def test_display_power_state_parses_ioreg_output():
    assert display_power_state(IOREG_ON) == 4
    assert display_power_state(IOREG_OFF) == 2
    assert display_power_state("") is None
    assert display_power_state('"MaxPowerState"=4') is None


def test_display_is_off_below_usable_state():
    # IODisplayWrangler 전원 상태 0~2 는 사용 불가(꺼짐·절전), 3 은 어둡게 켜짐, 4 는 켜짐.
    assert display_is_off(0) is True
    assert display_is_off(2) is True
    assert display_is_off(3) is False
    assert display_is_off(4) is False
    assert display_is_off(None) is False


def test_display_off_is_false_outside_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert display_off() is False
