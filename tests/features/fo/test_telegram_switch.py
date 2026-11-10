"""`/fo <계좌> on|off|기본` — 파생 계좌별 스위치(웹 `/settings/fo` 와 같은 control·이력 telegram).
`/fo` 조회는 누구나, 켜기·끄기는 신뢰된 발신자만. 실전 파생 계좌는 켤 수 없다."""

from __future__ import annotations

from types import SimpleNamespace

from tests.features.fo._support import account, settings
from tests.features.fo.conftest import NOW
from trader.features.fo import switch
from trader.features.fo.telegram import FO_CMD
from trader.kernel.ledger import control


def _ctx(conn, *, trusted=True, accounts=None):
    port = SimpleNamespace(fo_accounts=lambda: accounts or [account(), account(env="real")])
    return SimpleNamespace(conn=conn, settings=settings(enabled=False), actor="ohs",
                           trusted=trusted, now=lambda: NOW, ports={"accounts": port})  # fmt: skip


def test_untrusted_cannot_switch(conn):
    assert FO_CMD.untrusted_ok is True  # 조회는 그대로 누구나
    out = FO_CMD.handler(_ctx(conn, trusted=False), "paper-fo on")
    assert "거부" in out and control.get_flag(conn, switch.flag_key("paper-fo")) is None


def test_switch_on_off_default_and_audit(conn):
    ctx = _ctx(conn)
    assert "켜짐" in FO_CMD.handler(ctx, "paper-fo on")
    assert control.get_flag(conn, switch.flag_key("paper-fo")) is True
    row = conn.execute("SELECT channel, actor, key FROM control_log ORDER BY id DESC").fetchone()
    assert (row["channel"], row["actor"], row["key"]) == ("telegram", "ohs", "fo_enabled:paper-fo")
    assert "꺼짐" in FO_CMD.handler(ctx, "paper-fo off")
    assert "설정 파일" in FO_CMD.handler(ctx, "paper-fo 기본")
    assert control.get_flag(conn, switch.flag_key("paper-fo")) is None


def test_real_and_unknown_and_usage(conn):
    ctx = _ctx(conn)
    assert "거부" in FO_CMD.handler(ctx, "real-fo on")
    assert "알 수 없는" in FO_CMD.handler(ctx, "nobody on")
    assert "사용법" in FO_CMD.handler(ctx, "paper-fo maybe")
