"""`/status` 의 「성과」 절(계좌×시장별 최근 30일 수익률·MDD + RL 사이저 줄)과
「결정 분포(30일)」 절(action 별 건수·토론 수렴율)."""

from __future__ import annotations

from typing import Any

from trader.features.accounts.api import load_accounts, split_accounts
from trader.features.evaluation.metrics import convergence_rate, decision_distribution
from trader.features.evaluation.performance import metrics_of, nav_series
from trader.features.evaluation.rl import report as rl_report
from trader.kernel.registry.types import StatusSection


def _rl_lines(state: Any) -> list[str]:
    try:
        meta = rl_report.current_model(state)
    except Exception as e:  # noqa: BLE001 - 상태 조회는 실패해도 계속
        return [f"- rl_sizer 조회 실패: {e}"]
    if meta is None:
        return ["- rl_sizer 없음 (`uv run trader evaluation rl train`, 승격은 `rl promote` 수동)"]
    s = meta.get("summary") or {}
    rl, fx, llm = s.get("rl") or {}, s.get("fixed") or {}, s.get("llm") or {}
    return [
        f"- rl_sizer {meta.get('version')}: RL {rl.get('return_mean', '-')} / 고정 "
        f"{fx.get('return_mean', '-')} / LLM {llm.get('return_mean', '-')} · "
        f"{'승격' if meta.get('promoted') else '미승격'}"
    ]


def render(ctx: Any) -> list[str]:
    accounts, _ = split_accounts(load_accounts())
    out = []
    for a in accounts:
        for m in a.markets:
            met = metrics_of(nav_series(ctx.conn, a.id, m, days=30))
            if met["days"] == 0:
                continue
            ret, mdd = met["return"], met["mdd"]
            ret_s = "—" if ret is None else f"{ret:+.2%}"
            mdd_s = "—" if mdd is None else f"{mdd:+.2%}"
            out.append(f"- {a.id} {m}: 30일 {ret_s} · MDD {mdd_s}")
    return out + _rl_lines(getattr(ctx, "state", None))


STATUS_SECTION = StatusSection("성과", render)

DIST_DAYS = 30


def distribution_lines(ctx: Any) -> list[str]:
    """'- BUY 3 · HOLD 7 · SELL 1 · 수렴율 80%'. 결정·토론이 모두 없으면 [] (절 생략),
    수렴율은 토론 기록이 없으면(None) 뺀다."""
    now = ctx.now()
    dist = decision_distribution(ctx.conn, days=DIST_DAYS, now=now)
    rate = convergence_rate(ctx.conn, days=DIST_DAYS, now=now)
    parts = [f"{action} {n}" for action, n in dist.items()]
    if rate is not None:
        parts.append(f"수렴율 {rate:.0%}")
    return [f"- {' · '.join(parts)}"] if parts else []


DISTRIBUTION_SECTION = StatusSection(f"결정 분포({DIST_DAYS}일)", distribution_lines)
