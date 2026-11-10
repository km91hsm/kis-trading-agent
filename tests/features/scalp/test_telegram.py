"""`/scalp [<계좌> on|off|기본]` — 단타 계좌별 스위치(웹 `/settings/scalp` 와 같은 control, 이력
telegram). 인자 없으면 계좌별 상태. 실전 켜기는 웹에서 계좌 id 확인으로만(텔레그램은 거부)."""

from __future__ import annotations

from types import SimpleNamespace

from tests.features.accounts.conftest import make_account
from trader.features.scalp import switch
from trader.features.scalp.feature import FEATURE
from trader.features.scalp.telegram import SCALP_CMD
from trader.kernel.ledger import control


def _ctx(conn, settings):
    accts = [make_account("paper-main", "paper", "auto"), make_account("real-1", "real", "auto")]
    port = SimpleNamespace(accounts=lambda: accts)
    from datetime import UTC, datetime

    return SimpleNamespace(conn=conn, settings=settings, actor="ohs", trusted=True,
                           now=lambda: datetime(2026, 11, 10, tzinfo=UTC),
                           ports={"accounts": port})  # fmt: skip


def test_registered_trusted_only():
    assert SCALP_CMD in FEATURE.telegram_commands and SCALP_CMD.untrusted_ok is False


def test_list_and_switch(conn, settings):
    ctx = _ctx(conn, settings)
    listing = SCALP_CMD.handler(ctx, None)
    assert "paper-main" in listing and "real-1" in listing
    assert "꺼짐" in SCALP_CMD.handler(ctx, "paper-main off")
    assert control.get_flag(conn, switch.flag_key("paper-main")) is False
    row = conn.execute("SELECT channel, key FROM control_log ORDER BY id DESC").fetchone()
    assert (row["channel"], row["key"]) == ("telegram", "scalp_enabled:paper-main")
    assert "켜짐" in SCALP_CMD.handler(ctx, "paper-main on")
    assert "설정 파일" in SCALP_CMD.handler(ctx, "paper-main 기본")
    assert control.get_flag(conn, switch.flag_key("paper-main")) is None


def test_real_enable_refused_but_off_allowed(conn, settings):
    ctx = _ctx(conn, settings)
    assert "거부" in SCALP_CMD.handler(ctx, "real-1 on")
    assert control.get_flag(conn, switch.flag_key("real-1")) is None
    assert "꺼짐" in SCALP_CMD.handler(ctx, "real-1 off")
    assert "사용법" in SCALP_CMD.handler(ctx, "paper-main maybe")
