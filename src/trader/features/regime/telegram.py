"""regime 텔레그램 명령 `/kill`(킬스위치 켜기)과 `/status` 의 「국면」 절.

`/kill` 은 켜기만 한다 — 해제는 원인 메모와 함께 CLI(`trader regime killswitch off`)로만
(Stock D-049). 신뢰할 수 없는 발신자는 쓸 수 없다(2-T2)."""

from __future__ import annotations

from trader.features.regime import killswitch, messages
from trader.features.regime import regime as regime_mod
from trader.kernel.notify.commands import CommandContext
from trader.kernel.registry.types import StatusSection, TelegramCommand

KILL_REASON = "텔레그램 /kill"


def handle_kill(ctx: CommandContext, arg: str | None) -> str:
    killswitch.engage(ctx.conn, reason=KILL_REASON, source="telegram")
    return messages.killswitch_on(KILL_REASON)


KILL = TelegramCommand(
    "kill", untrusted_ok=False, help_line="/kill — 신규 매수 중단(킬스위치)", handler=handle_kill
)

def status_lines(ctx: CommandContext) -> list[str]:
    """`/status` 국면 한 줄. 설정이 없는 문맥(settings=None)이면 시장 목록을 몰라 줄을 내지 않는다
    — 절 하나 때문에 /status 전체가 실패하지 않게."""
    if ctx.settings is None:
        return []
    return [regime_mod.line(ctx.conn, ctx.settings.markets)]


STATUS_SECTION = StatusSection("국면", status_lines)
