"""커널 내장 점검 영역: `jobs`(데몬 생존·하트비트·놓친 잡·24h 오류) · `llm`(세션 상한·구독 사용률·
세션 실패율) · `orders`(레이트리미터·unknown·마감 뒤 미정리·승인 만료·대기). 현물(`asset='stock'`)
주문만 센다 — 파생 주문은 fo 기능 영역이 본다."""

from __future__ import annotations

from datetime import timedelta

from trader.kernel.broker import rate_store
from trader.kernel.config.market_hours import market_close
from trader.kernel.config.time import MARKET_TZ
from trader.kernel.daemon.activity import recent_errors
from trader.kernel.daemon.heartbeat import read_status
from trader.kernel.execution.ledger import day_bounds, pending_approvals
from trader.kernel.execution.types import dead_status_sql
from trader.kernel.ops.types import Item, OpsContext
from trader.kernel.registry.types import HealthArea
from trader.kernel.session import usage
from trader.kernel.session.calls import FAKE_MODEL, ORCHESTRATOR, sessions_today
from trader.kernel.session.run_store import run_date_for

LLM_CAP_WATCH = 0.8
DAY = timedelta(hours=24)


def check_jobs(ctx: OpsContext) -> list[Item]:
    out: list[Item] = []
    st = read_status(ctx.state, ctx.now, list(ctx.jobs))
    if not st["alive"]:
        text = "데몬이 꺼져 있어요 (daemon.lock 없음·pid 사망)"
        out.append(Item("jobs.daemon", "critical", text, link="/system"))
    elif st["stale"]:
        out.append(
            Item(
                "jobs.stale",
                "critical",
                f"데몬이 살아 있지만 하트비트가 {int(st['age_s'] or 0) // 60}분 멈춤",
                link="/system",
            )
        )
    missed = [j["id"] for j in st["jobs"] if j.get("last_status") == "missed"]
    if missed:
        out.append(
            Item(
                "jobs.missed",
                "warning",
                f"놓친 잡 {len(missed)}건: {', '.join(missed[:4])}",
                link="/system",
                count=len(missed),
            )
        )
    counts = recent_errors(ctx.conn, ctx.now)
    n = counts["total"]
    if n:
        out.append(
            Item(
                "jobs.errors",
                "warning" if counts["failed"] >= 3 else "watch",
                f"24시간 오류 이력 {n}건 · 확인 필요 {counts['failed']}건 · "
                f"후속 정상 실행 {counts['followed_by_success']}건 · "
                f"종료 요청 중단 {counts['interrupted']}건"
                " — 대상 범위 미확인 이력은 확인 필요에 포함, 현재 장애·원인 해결 여부는 별도 확인",
                link="/system",
                count=n,
            )
        )
    return out


def check_llm(ctx: OpsContext) -> list[Item]:
    out: list[Item] = []
    cap, _ = usage.session_cap(ctx.conn, ctx.settings)
    n = sessions_today(ctx.conn, ctx.now)
    if cap and n >= cap:
        out.append(
            Item(
                "llm.cap",
                "warning",
                f"오늘 세션 {n}/{cap} — 상한 도달, 남은 분석·재평가는 거부됨",
                link="/system",
            )
        )
    elif cap and n >= cap * LLM_CAP_WATCH:
        out.append(Item("llm.cap", "watch", f"오늘 세션 {n}/{cap} — 상한 근접", link="/system"))
    # 구독 사용률 감속: soft 면 watch(새 분석 생략), hard 면 warning(세션 전부 중지)
    ok_new, why = usage.session_allowance(ctx.conn, ctx.settings, kind="analyze", now=ctx.now)
    if not ok_new:
        ok_re, why_re = usage.session_allowance(ctx.conn, ctx.settings, kind="resume", now=ctx.now)
        out.append(
            Item(
                "llm.usage",
                "watch" if ok_re else "warning",
                why if ok_re else (why_re or why or "구독 사용률 감속"),
                link="/system",
            )
        )
    row = ctx.conn.execute(
        "SELECT COUNT(*) AS n, SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS e "
        "FROM agent_calls WHERE agent=? AND COALESCE(model,'') != ? AND created_at >= ?",
        (ORCHESTRATOR, FAKE_MODEL, (ctx.now - DAY).isoformat()),
    ).fetchone()
    total, err = int(row["n"] or 0), int(row["e"] or 0)
    if total >= 3 and err / total >= 0.5:
        out.append(
            Item(
                "llm.errors",
                "critical",
                f"24시간 세션 {total}건 중 {err}건 실패 — claude CLI·인증·네트워크 확인",
                link="/system",
                count=err,
            )
        )
    elif err:
        text = f"24시간 세션 {total}건 중 {err}건 실패"
        out.append(Item("llm.errors", "watch", text, link="/system", count=err))
    return out


def _stale_orders(ctx: OpsContext) -> int:
    """현물 계좌 시장마다 마감 뒤에도 살아 있는 주문 수(현지 오늘 이전, 마감 뒤면 오늘 포함)."""
    port = ctx.ports.get("accounts")
    if port is None:
        return 0
    accounts = [a for a in port.accounts() if getattr(a, "asset", "stock") == "stock"]
    stale = 0
    for m in sorted({mm for a in accounts for mm in a.markets}):
        d = run_date_for(m, ctx.now)
        local_now = ctx.now.astimezone(MARKET_TZ[m])
        closed_today = local_now.time() >= market_close(m, local_now.date())
        bound = day_bounds(d + timedelta(days=1) if closed_today else d, m)[0]
        row = ctx.conn.execute(
            "SELECT COUNT(*) FROM orders WHERE market=? AND created_at < ? AND asset='stock' "
            f"AND status NOT IN ({dead_status_sql()},'filled','unknown')",
            (m, bound),
        ).fetchone()
        stale += int(row[0])
    return stale


def check_orders(ctx: OpsContext) -> list[Item]:
    out: list[Item] = []
    for d in rate_store.disabled_status():  # 같은 프로세스의 리미터만 보인다
        out.append(
            Item(
                "orders.rate_limiter",
                "warning",
                f"KIS 교차 프로세스 레이트리미터 꺼짐({d['env']}, {d['error']}) — 데몬과 "
                "분석 도구가 초당 한도를 나누지 못해 EGW00201 이 날 수 있음. "
                "state/kis_rate.db 권한·디스크 확인",
                link="/system",
            )
        )
    n_unknown = int(
        ctx.conn.execute(
            "SELECT COUNT(*) FROM orders WHERE status='unknown' AND asset='stock'"
        ).fetchone()[0]
    )
    if n_unknown:
        out.append(
            Item(
                "orders.unknown",
                "warning",
                f"응답 없는 주문(unknown) {n_unknown}건 — 체결 동기화가 확정하지 못함, KIS 앱 확인",
                link="/",
                count=n_unknown,
            )
        )
    stale = _stale_orders(ctx)
    if stale:
        out.append(
            Item(
                "orders.stale",
                "watch",
                f"마감 뒤에도 정리되지 않은 주문 {stale}건 (체결 동기화·대조 확인)",
                link="/",
                count=stale,
            )
        )
    n_exp = int(
        ctx.conn.execute(
            "SELECT COUNT(*) FROM approvals WHERE status='expired' AND requested_at >= ?",
            ((ctx.now - DAY).isoformat(),),
        ).fetchone()[0]
    )
    if n_exp:
        out.append(
            Item(
                "orders.approvals_expired",
                "watch",
                f"24시간 안에 만료된 승인 요청 {n_exp}건 (응답 없이 지나감)",
                link="/",
                count=n_exp,
            )
        )
    pending = pending_approvals(ctx.conn)
    if pending:
        out.append(
            Item(
                "orders.approvals_pending",
                "watch",
                f"승인 대기 {len(pending)}건 (30분 안에 승인·거부)",
                link="/",
                count=len(pending),
            )
        )
    return out


KERNEL_AREAS: tuple[HealthArea, ...] = (
    HealthArea("jobs", check_jobs),
    HealthArea("llm", check_llm),
    HealthArea("orders", check_orders),
)
