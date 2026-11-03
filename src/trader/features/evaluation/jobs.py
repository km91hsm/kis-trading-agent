"""evaluation 잡·훅: `scan_analyze.post` 섀도 기록 훅, 일일 리포트·모의 체결 점검 잡."""

from __future__ import annotations

import logging

from pydantic import BaseModel, ConfigDict, Field

from trader.features.accounts.api import split_accounts
from trader.features.evaluation import daily_report, open_window, paper_review, shadow
from trader.features.evaluation.memory import outcomes, store
from trader.features.evaluation.reflect import pending, run_reflect
from trader.features.evaluation.rl import train as rl_train_mod
from trader.features.ingest.api import load_universe
from trader.features.ml import api as ml_api
from trader.features.regime import api as regime_api
from trader.features.scan import api as scan_api
from trader.kernel.config import KST, Market
from trader.kernel.lane.types import LaneContext
from trader.kernel.notify import messages
from trader.kernel.registry import hooks as hooks_mod
from trader.kernel.registry.types import JobSpec, QueueKind
from trader.kernel.session import run_store
from trader.kernel.session.runner import is_fake

logger = logging.getLogger("trader.evaluation")


def shadow_record_hook(*, deps, market: str, result, **_) -> None:
    """섀도 포트폴리오(전체 검토 L1): 오늘 scan 후보·LLM 결정으로 비교 포트폴리오를 남긴다.
    주문 없음, 실패해도 잡은 계속(경고만). 전부 실패·중단·scan 없음인 날과 FAKE 세션은 남기지
    않는다 — 운영 실패나 고정 HOLD 를 LLM 선택으로 채점하지 않게(PR #33). 일부 종목만 실패한 날은
    성공한 결정으로 기록하되 실패 종목은 후보 기준선에서도 뺀다(LLM 이 고를 수 없었던 종목 —
    같은 후보 집합 비교, 2-L6)."""
    a = result if isinstance(result, dict) else {}
    failed = {str(t) for t in (a.get("failed") or [])}
    if a.get("skipped") or a.get("stopped") or a.get("error") or (failed and not a.get("ok")):
        logger.info("섀도 기록 생략 %s: 분석 미완(%s)", market, a)
        return
    try:
        if failed:
            logger.info("섀도 기록 %s: 실패 종목 제외 %s", market, ", ".join(sorted(failed)))
        now = deps.now()
        today = run_store.run_date_for(market, now)
        scan = run_store.read_checkpoint(run_store.market_dir(today, market, deps.state), "_scan")
        if not scan or is_fake() or ((scan.get("session") or {}).get("fake")):
            return
        accounts = (deps.ports or {}).get("accounts")
        client = accounts.market_client() if accounts is not None else None
        conn = deps.conn_factory()
        try:
            index = shadow.index_table(client, deps.settings.hedge, market, now=now)
            shadow.record(
                conn,
                market=market,
                day=today,
                scan=scan,
                now=now,
                index=index,
                exclude=frozenset(failed),
            )
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 - 측정 실패가 분석 잡을 실패로 만들지 않는다
        logger.warning("섀도 포트폴리오 기록 실패 %s: %s", market, e)


def _accounts(deps) -> list:
    port = (deps.ports or {}).get("accounts")
    stock, _ = split_accounts(list(port.accounts())) if port is not None else ([], [])
    return stock


def daily_report_body(deps, job) -> dict:
    """장 마감 일일 리포트 → 텔레그램(오늘 NAV 행이 있는 계좌만, 없으면 조용히). 체결 선동기화는
    하지 않는다(레인 호출 — US 는 실현손익이 하루 늦을 수 있다, README)."""
    conn = deps.conn_factory()
    try:
        r = daily_report.compute(
            conn,
            _accounts(deps),
            markets=[job.market],
            today_of=lambda m: run_store.run_date_for(m, deps.now()),
        )
    finally:
        conn.close()
    if r["items"]:
        deps.notifier.send(daily_report.message(r))
    return {"items": len(r["items"])}


def paper_review_body(deps, job=None) -> dict:
    """모의 체결 대 가정 주간 점검(토 09:00 KST): 지난 7일 결정 주문 체결률·슬리피지 → 텔레그램."""
    conn = deps.conn_factory()
    try:
        r = paper_review.weekly(conn, _accounts(deps), today=deps.now().astimezone(KST).date())
    finally:
        conn.close()
    if any(it["terminal"] for it in r["items"]) or r["alerts"]:
        deps.notifier.send(paper_review.message(r))
    return {"items": len(r["items"]), "alerts": len(r["alerts"])}


DAILY_REPORT = JobSpec(
    id="{market}_daily_report", kind="daily_report", trigger="cron", per_market=True,
    schedule_key="daily_report.{market}", interval_s=None, enabled=None, gate="market",
    heavy=False, misfire="default", catch_up="notify", start_soon_s=0, body=daily_report_body,
    days="mon-fri",
)  # fmt: skip

PAPER_REVIEW = JobSpec(
    id="paper_review", kind="paper_review", trigger="cron", per_market=False,
    schedule_key="paper_review", interval_s=None, enabled=None, gate="none", heavy=False,
    misfire="idempotent", catch_up="notify", start_soon_s=0, body=paper_review_body,
)  # fmt: skip


def _open_window_targets(deps, conn, markets) -> list[tuple[str, str, str | None]]:
    """일일 스냅샷에 더할 유니버스 밖 열린 창 종목(보유·미만기 메모리·미만기 섀도, 2-L1). 실패는
    경고만(스냅샷은 유니버스로 계속)."""
    try:
        universe = load_universe()
        return [
            t
            for m in markets
            for t in open_window.targets(
                conn, m, universe, exchanges=open_window._exchanges(conn, m)
            )
        ]
    except Exception as e:  # noqa: BLE001
        logger.warning("열린 창 종목 수집 실패: %s", e)
        return []


def _lane(deps, conn) -> LaneContext:
    return LaneContext(
        settings=deps.settings,
        conn=conn,
        state=deps.state,
        now=deps.now,
        dry_run=True,
        notifier=deps.notifier,
        ports=deps.ports,
    )


def reflect_body(deps, job=None, *, market: str | None = None) -> dict:
    """장 마감 후: 피처 스냅샷 → 만기 결정 결과 채우기 → 시장 제안·테마·섀도 채점 → `reflect.mature`
    훅 → 반성 대기가 있는 시장만 세션 → 메모리 정리. 보존 정리(retention)는 M12 로 미룬다."""
    markets = list(deps.markets)
    today = deps.now().astimezone(KST).date()
    ports = deps.ports or {}
    accounts = ports.get("accounts")
    mc = accounts.market_client() if accounts is not None else None
    ml = ports.get("ml")
    features_n: int | None = None
    conn = deps.conn_factory()
    try:
        if mc is not None and ml is not None:
            extra = _open_window_targets(deps, conn, markets)
            try:
                features_n = int(ml.snapshot(list(markets), extra_targets=extra))
            except Exception as e:  # noqa: BLE001 - 스냅샷 실패는 통보하고 반성은 계속
                logger.warning("피처 스냅샷 실패: %s", e)
                deps.notifier.send(
                    messages.event("피처 스냅샷 실패", f"{e} — 만기 결과가 채워지지 않을 수 있어요")
                )
        matured = outcomes.mature(conn, state=deps.state, today=today)
        views_matured = 0
        index_tables = {}
        for m in markets:
            idx = shadow.index_table(mc, deps.settings.hedge, m, now=deps.now())
            index_tables[m] = idx
            try:
                candles = (
                    []
                    if idx.empty
                    else [(d, float(v)) for d, v in idx.iloc[:, 0].items()]
                )
                views_matured += regime_api.mature_views(
                    conn, m, today=run_store.run_date_for(m, deps.now()), candles=candles
                )
            except Exception as e:  # noqa: BLE001 - 채점 실패가 복기를 막지 않는다
                logger.warning("시장 제안 채점 실패 %s: %s", m, e)
        for m in markets:  # 테마 열기 판정 사후 검증 — 실패는 경고만
            try:
                scan_api.theme_mature(
                    conn,
                    m,
                    today=today,
                    close_table=lambda mk, s, u: ml_api.close_table(
                        deps.state, mk, since=s, until=u
                    ),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("테마 판정 채점 실패 %s: %s", m, e)
        try:
            shadow_matured = shadow.mature(
                conn, state=deps.state, today=today, index_tables=index_tables
            )
        except Exception as e:  # noqa: BLE001 - 섀도 만기 실패가 반성을 막지 않는다
            logger.warning("섀도 포트폴리오 만기 실패: %s", e)
            shadow_matured = None
        hooks_mod.run(
            deps.hooks, "reflect.mature", deps=deps, conn=conn, today=today, matured=matured
        )
        sessions = 0
        errors: list[str] = []
        for m in [market] if market else markets:
            if not pending(conn, market=m):
                continue
            if deps.stop_event.is_set():
                logger.warning("종료 중: reflect 세션 생략 %s", m)
                break
            try:
                res = run_reflect(_lane(deps, conn), market=m, date=today, cancel=deps.stop_event)
            except Exception as e:  # noqa: BLE001 - 한 시장의 실패가 다른 시장을 막지 않는다
                logger.exception("reflect 실패 %s", m)
                errors.append(f"{m}: {type(e).__name__}: {e}")
                deps.notifier.send(messages.event("복기 실패", f"{m}: {e}"))
                continue
            if res.session_id or res.fake:
                sessions += 1
            if not res.ok:
                errors.append(f"{m}: {res.error}")
                deps.notifier.send(messages.event("복기 실패", f"{m}: {res.error}"))
        pr = store.promote_and_prune(conn, now=deps.now(), settings=deps.settings.memory)
    finally:
        conn.close()
    return {
        "features": features_n,
        "matured": len(matured),
        "views_matured": views_matured,
        "shadow_matured": shadow_matured,
        "sessions": sessions,
        "errors": errors,
        "pruned": pr["deleted"],
    }


class ReflectParams(BaseModel):
    """큐 `reflect` 요청: 지정 시장만(없으면 전체)."""

    model_config = ConfigDict(extra="forbid")

    market: Market | None = None


def reflect_request(deps, params: dict, progress) -> dict:
    """큐 `reflect` 본문."""
    return reflect_body(deps, market=params.get("market"))


REFLECT_JOB = JobSpec(
    id="reflect", kind="reflect", trigger="cron", per_market=False,
    schedule_key="reflect", interval_s=None, enabled=None, gate="none", heavy=True,
    misfire="idempotent", catch_up="queue", start_soon_s=0, body=reflect_body,
    days="mon-fri",
)  # fmt: skip

def rl_train_body(deps, job=None, *, market: str | None = None, seeds: int | None = None):
    """사이저 학습(승격은 `trader evaluation rl promote` 수동). `rl_sizer.enabled` 는 적용
    스위치이지 학습 스위치가 아니라 주간 학습은 늘 돈다. 데이터 부족은 로그만, 실패는 통보."""
    try:
        cfg = ml_api.load_ml(deps.registry)
        if seeds is not None:
            rl_cfg = cfg.models.rl_sizer.model_copy(update={"seeds": seeds})
            cfg = cfg.model_copy(
                update={"models": cfg.models.model_copy(update={"rl_sizer": rl_cfg})}
            )
        conn = deps.conn_factory()
        try:
            r = rl_train_mod.train_sizer(cfg, conn, state=deps.state, now=deps.now, market=market)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 - 실험 학습 실패가 데몬을 막지 않는다
        logger.exception("RL 사이저 학습 실패")
        deps.notifier.send(messages.failure("RL 학습 실패", "", e))
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}
    if r.get("status") == "insufficient_data":
        logger.info("RL 사이저 학습 생략: %s", r.get("promote_reason") or "데이터 부족")
        return r
    s = (r.get("summary") or {}).get("rl") or {}
    deps.notifier.send(
        ml_api.ml_trained(
            kind="RL 사이저",
            version=r.get("version"),
            trained=r.get("status") == "trained",
            detail=f"RL 수익 평균 {s.get('return_mean', '-')}",
            promoted=None,
            note="승격은 `trader evaluation rl promote` 로 수동이에요.",
        )
    )
    return r


class RlTrainParams(BaseModel):
    """큐 `rl_train` 요청: 시장(없으면 전체)·시드 수(없으면 ml.yaml)."""

    model_config = ConfigDict(extra="forbid")

    market: Market | None = None
    seeds: int | None = Field(None, ge=1, le=10)


def rl_train_request(deps, params: dict, progress) -> dict:
    """큐 `rl_train` 본문."""
    return rl_train_body(deps, market=params.get("market"), seeds=params.get("seeds")) or {}


RL_TRAIN = JobSpec(
    id="rl_train", kind="rl_train", trigger="cron", per_market=False,
    schedule_key="rl_train", interval_s=None, enabled=None, gate="none", heavy=True,
    misfire="idempotent", catch_up="none", start_soon_s=0, body=rl_train_body,
)  # fmt: skip

QUEUE_KINDS = (
    QueueKind("reflect", ReflectParams, "복기", "slow", 0, False, False, reflect_request),
    QueueKind("rl_train", RlTrainParams, "RL 학습", "slow", 0, False, False, rl_train_request),
)
