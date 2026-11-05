"""regime 텔레그램 명령 `/kill`(킬스위치 켜기)과 `/status` 의 「국면」 절(국면·헤지·제안 적중률).

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

def _hedge_lines(conn, markets: list[str], h) -> list[str]:
    """헤지 설정 한 줄 + 시장 세션이 켜져 있으면 제안 적중률 한 줄(옛 STATUS `_regime_lines`)."""
    if not h.enabled:
        return ["- 헤지 비활성 (hedge.enabled=false)"]
    inst = ", ".join(
        f"{m} {d['inverse'].symbol if d.get('inverse') else '-'}"
        + (f"/{d['inverse2x'].symbol}" if h.leverage and d.get("inverse2x") else "")
        for m, d in h.instruments.items()
    )
    out = [f"- 헤지: {inst} 최대 {h.max_weight_pct:.0%} · bear 기준 {h.bear_min_signals}/3"]
    if h.market_session:
        parts = []
        for r in regime_mod.summary(conn, markets):
            sc = r.get("scorecard")
            if not sc:
                parts.append(f"{r['market']} -")
            elif sc["matured"]:
                parts.append(
                    f"{r['market']} {sc['hits']}/{sc['matured']}({sc['hit_rate']:.0%}, "
                    f"제안 {sc['n']}건)"
                )
            else:
                parts.append(f"{r['market']} 만기 전 {sc['n']}건")
        out.append(f"- 제안 적중률: {' · '.join(parts)}")
    return out


def status_lines(ctx: CommandContext) -> list[str]:
    """`/status` 국면 줄 + 헤지 줄. 설정이 없는 문맥(settings=None)이면 시장 목록을 몰라 줄을
    내지 않는다 — 절 하나 때문에 /status 전체가 실패하지 않게."""
    if ctx.settings is None:
        return []
    markets = list(ctx.settings.markets)
    return [
        f"- {regime_mod.line(ctx.conn, markets)}",
        *_hedge_lines(ctx.conn, markets, ctx.settings.hedge),
    ]


STATUS_SECTION = StatusSection("국면", status_lines)
