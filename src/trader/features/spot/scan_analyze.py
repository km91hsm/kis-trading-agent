"""spot 의 아침 잡 `{market}_scan_analyze`: 스캔(`ports["scan"].run_scan`) → 종목별 분석 세션.

옛 데몬 `_scan_analyze_body`·`_analyze_body`·`_analyze_targets` 의 이식. 큐 kind `scan_analyze`
(수동 스캔→분석)와 `analyze_ticker`(단일 종목 분석)도 여기서 맡는다. 국면·시장 세션은 이 잡 앞에서
별도 크론(`regime`)이 돌고, 옛 섀도 기록은 M8 이라 `scan_analyze.pre`/`post` 훅 자리만 둔다.
다른 기능(scan·ml·ingest)은 import 하지 않고 `deps.ports` 로만 쓴다."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trader.features.spot import locks, tools
from trader.features.spot.schemas import CHECKPOINT_NAMES
from trader.features.spot.session_kinds import AGENTS, ANALYZE
from trader.kernel.execution import core, horizon
from trader.kernel.execution import ledger as exec_ledger
from trader.kernel.lane.types import LaneContext
from trader.kernel.ledger import control, names
from trader.kernel.notify import messages
from trader.kernel.notify.telegram import NullNotifier
from trader.kernel.platform import paths
from trader.kernel.registry import hooks
from trader.kernel.registry.types import JobSpec, QueueKind
from trader.kernel.session import usage
from trader.kernel.session.agents import by_name
from trader.kernel.session.run_store import market_dir, read_checkpoint, run_date_for
from trader.kernel.session.runner import SessionSpec, ThrottleBlocked, is_fake, run_session

logger = logging.getLogger("trader.spot")


def _send(deps, text: str) -> None:
    if deps.notifier is not None:
        core.notify_quietly(deps.notifier, text)


def _cache_dir(deps) -> Path:
    return (deps.state or paths.state_dir()) / "cache"


def throttle_notice(deps, market: str, why: str) -> None:
    """사용량 감속으로 분석을 쉰 날 알림 1번(시장·운영일 단위)."""
    key = f"throttle_notice:{market}:{run_date_for(market, deps.now()).isoformat()}"
    conn = deps.conn_factory()
    try:
        if control.get_flag(conn, key):
            return
        control.set_flag(conn, key, deps.now().isoformat())
    finally:
        conn.close()
    _send(deps, messages.throttle_skip(market=market, why=why))


def skip_held_by_profile(
    deps, conn, market: str, targets: list[dict], today: date
) -> tuple[list[dict], list[dict]]:
    """보유 종목(`source == "held"`, picks·watch 에 없는 것)은 최근 결정의 보유기간 프로필
    `daily_reanalyze_every` 운영일마다만 아침 재분석한다(Phase 23 T). 그 사이 날은 monitor 손절·
    트리거·뉴스 재평가가 본다. 결정이 없는 보유분은 항상 분석."""
    keep, skipped = [], []
    for t in targets:
        if t.get("source") != "held":
            keep.append(t)
            continue
        latest = exec_ledger.latest_decision(conn, market, t["ticker"])
        if latest is None:
            keep.append(t)
            continue
        name, prof = horizon.of_decision(deps.settings.risk, latest.get("data"))
        last_d = date.fromisoformat(str(latest["date"]))
        if horizon.reanalyze_due(prof, last_d, today):
            keep.append(t)
        else:
            skipped.append(
                {
                    "ticker": t["ticker"],
                    "profile": name,
                    "last": last_d.isoformat(),
                    "every": prof.daily_reanalyze_every,
                }
            )
    if skipped:
        logger.info("%s 보유 재분석 건너뜀(프로필 주기): %s", market, skipped)
    return keep, skipped


def snapshot_listed(deps, market: str, targets: list[dict]) -> None:
    """유니버스 밖 발굴 종목은 피처 스토어에 행이 없어 05_quant·섹터 비교가 빈다 → 분석 앞에서 그
    종목들을 **모아** 스냅샷 1회(`ports["ml"].snapshot(tickers=)`; 종목마다 부르면 수정주가 감지가
    스토어를 그만큼 다시 읽는다, 2-P9). ml 포트가 없으면 건너뛰고 실패는 경고만."""
    ml = deps.ports.get("ml")
    if ml is None or not targets:
        return
    names_ = ", ".join(str(t["ticker"]) for t in targets)
    try:
        n = ml.snapshot([market], tickers=[(t["ticker"], t.get("exchange")) for t in targets])
        logger.info("발굴 종목 피처 스냅샷 %s %s: %s행", market, names_, n)
    except Exception as e:  # noqa: BLE001 - 피처 없이도 세션은 돈다
        logger.warning("발굴 종목 피처 스냅샷 실패 %s %s: %s", market, names_, e)


def _collect(deps, market: str, t: dict, today: date) -> None:
    """종목 **직전**에 입력을 수집하고 05_quant 를 만든다: 전 종목을 먼저 모으면 마지막 종목 입력이
    세션 때 수십 분 늙는다."""
    inputs = deps.ports.get("inputs")
    if inputs is None:
        return
    market_client = None
    accounts = deps.ports.get("accounts")
    if accounts is not None:
        try:
            market_client = accounts.market_client()
        except Exception as e:  # noqa: BLE001 - 시세 없이도 수집은 돈다
            logger.warning("시세 클라이언트 확인 실패: %s", type(e).__name__)
    try:
        inputs.collect(
            ticker=t["ticker"],
            market=market,
            exchange=t.get("exchange"),
            asof=deps.now(),
            force=False,
            conn_factory=deps.conn_factory,
            market_client=market_client,
            state=deps.state,
        )
    except Exception as e:  # noqa: BLE001 - 한 종목 수집 실패는 세션이 다시 시도한다
        logger.warning("수집 실패 %s %s: %s", market, t["ticker"], e)
    _ensure_quant(deps, t["ticker"], market, today)


def _ensure_quant(deps, ticker: str, market: str, today: date) -> None:
    conn = deps.conn_factory()
    try:
        ctx = tools.ctx_from_ports(
            settings=deps.settings,
            conn=conn,
            state=deps.state,
            ports=deps.ports,
            notifier=deps.notifier,
            dry_run=bool(deps.dry_run),
            now=deps.now,
        )
        try:
            tools.ensure_quant(ctx, ticker=ticker, market=market, date=today)
        except Exception as e:  # noqa: BLE001 - 05_quant 는 선택 입력
            logger.warning("05_quant 생성 실패 %s %s: %s", market, ticker, e)
        try:
            tools.ensure_calibration(ctx, ticker=ticker, market=market, date=today)
        except Exception as e:  # noqa: BLE001 - 35_calibration 은 선택 입력
            logger.warning("35_calibration 생성 실패 %s %s: %s", market, ticker, e)
    except Exception as e:  # noqa: BLE001 - 도구 컨텍스트 조립 실패도 세션을 막지 않는다
        logger.warning("05_quant·35_calibration 준비 실패 %s %s: %s", market, ticker, e)
    finally:
        conn.close()


def analyze_targets(
    deps, market: str, targets: list[dict], *, today: date, dry_run: bool | None = None
) -> dict:
    """대상 종목마다 수집 → 05_quant → 세션 1회. dry_run 이 None 이면 데몬 설정을 따른다
    (대기열의 단일 종목 분석은 `deps.dry_run or 요청값` 으로 데몬 --dry-run 을 뒤집지 못한다)."""
    dry = deps.dry_run if dry_run is None else (deps.dry_run or dry_run)
    if not is_fake():
        # 감속 중이면 수집(KIS 호출)부터 하지 않는다 — 세션이 어차피 막힌다
        conn = deps.conn_factory()
        try:
            allowed, why = usage.session_allowance(
                conn, deps.settings, kind="analyze", now=deps.now()
            )
        finally:
            conn.close()
        if not allowed:
            throttle_notice(deps, market, why or "구독 사용률 감속")
            return {"targets": len(targets), "skipped": f"사용량 감속 — {why}"}

    snapshot_listed(deps, market, [t for t in targets if t.get("discovery") == "listed"])
    # 종목당 세션 1회(--max-budget-usd 는 세션 단위). 실패한 종목은 기록하고 다음 종목으로.
    ok, failed, sessions = 0, [], []
    stopped = False
    for t in targets:
        if deps.stop_event.is_set():
            # SIGTERM 뒤 shutdown(wait=True) 가 이 잡을 기다린다. 남은 종목 세션을 계속 띄우면
            # launchd ExitTimeOut(세션 1회 기준) 안에 못 끝나 SIGKILL 된다
            left = len(targets) - ok - len(failed)
            logger.warning("종료 중: 남은 analyze 세션 %d건 생략 %s", left, market)
            stopped = True
            break
        _collect(deps, market, t, today)
        conn = deps.conn_factory()
        try:
            shown = names.display(conn, _cache_dir(deps), market, t["ticker"])
            lane = LaneContext(
                settings=deps.settings,
                conn=conn,
                state=deps.state,
                now=deps.now,
                dry_run=bool(dry),
                notifier=deps.notifier or NullNotifier(),
                ports=deps.ports,
                ext={"registry": deps.registry},
            )
            try:
                with locks.lock_for(market, t["ticker"]):
                    res = run_session(
                        SessionSpec(
                            market=market, date=today, tickers=[t], dry_run=dry, asof=deps.now()
                        ),
                        ANALYZE,
                        lane,
                        agents=by_name(AGENTS),
                        cancel=deps.stop_event,
                        checkpoint_names=CHECKPOINT_NAMES,
                    )
            except ThrottleBlocked as e:
                # 도중에 감속 기준을 넘었다 — 남은 종목도 같은 이유로 막히니 멈춘다(실패 아님)
                logger.info("analyze 생략(사용량 감속) %s: %s", market, e)
                throttle_notice(deps, market, str(e))
                return {"targets": len(targets), "ok": ok, "failed": failed, "sessions": sessions,
                        "skipped": f"사용량 감속 — {e}"}  # fmt: skip
            except Exception as e:  # noqa: BLE001 - 한 종목 세션 실패가 나머지를 막지 않는다
                logger.exception("analyze 세션 기동 실패 %s %s", market, t["ticker"])
                failed.append(t["ticker"])
                _send(deps, messages.failure("분석 실패", f"{market} {shown}", e))
                continue
        finally:
            conn.close()
        if res.ok:
            ok += 1
            sessions.append(
                {"ticker": t["ticker"], "session_id": res.session_id, "cost_usd": res.cost_usd}
            )
        else:
            failed.append(t["ticker"])
            _send(deps, messages.event("분석 실패", f"{market} {shown}: {res.error}"))
    return {
        "targets": len(targets),
        "ok": ok,
        "failed": failed,
        "sessions": sessions,
        "stopped": stopped,
    }


def analyze_body(deps, market: str, *, scan_error: str | None = None) -> dict:
    """`_scan.json` 의 분석 대상으로 `analyze_targets` 를 돌린다."""
    today = run_date_for(market, deps.now())
    scan = read_checkpoint(market_dir(today, market, deps.state), "_scan")
    if scan is None:
        # scan 이 실패했거나 결과가 없다. 조용히 ok 로 끝나면 그 시장의 하루 분석이 통째로 빠진
        # 것을 로그 말고는 알 수 없다 → 원인을 담은 알림 1건 + error 행
        cause = f"scan 실패: {scan_error}" if scan_error else "_scan.json 없음 (scan 지연·실패)"
        msg = messages.event("분석 생략", f"{market} {today}", f"{cause}, 보유 재분석도 생략")
        logger.error(msg)
        _send(deps, msg)
        return {"targets": 0, "error": cause}
    targets = scan.get("analyze_targets") or []
    if not targets:
        logger.info("%s 분석 대상 없음 (빈 picks·보유·관심종목)", market)
        return {"targets": 0}
    conn = deps.conn_factory()
    try:
        targets, skipped = skip_held_by_profile(deps, conn, market, targets, today)
    finally:
        conn.close()
    out = analyze_targets(deps, market, targets, today=today) if targets else {"targets": 0}
    if skipped:
        out["skipped_held"] = skipped
    return out


def scan_analyze_body(deps, job) -> dict:
    """scan 뒤 바로 analyze. scan 이 예외로 죽어도 통보하고 analyze 를 시도한다(analyze_body 가
    `_scan.json` 없음을 알림·error 로 남긴다). 세션을 세기 전에 stale running 을 정리한다.
    국면은 별도 크론(`regime`)이 5분 앞서 돌고, 이 앞뒤로 `scan_analyze.pre`/`post` 훅이 돈다."""
    return _scan_analyze(deps, job.market)


def _close_stale(deps) -> None:
    from trader.features.spot import jobs as spot_jobs  # jobs 가 이 모듈을 import 한다(순환)

    spot_jobs._close_stale_sessions(deps)


def _scan_analyze(deps, market: str) -> dict:
    _close_stale(deps)
    scan = deps.ports.get("scan")
    if scan is None:
        return {"error": "scan 포트 없음"}
    hooks.run(deps.hooks, "scan_analyze.pre", deps=deps, market=market)
    scan_error: str | None = None
    conn = deps.conn_factory()
    try:
        lane = LaneContext(
            settings=deps.settings,
            conn=conn,
            state=deps.state,
            now=deps.now,
            dry_run=bool(deps.dry_run),
            notifier=deps.notifier or NullNotifier(),
            ports=deps.ports,
            ext={"registry": deps.registry},
        )
        try:
            scan.run_scan(lane, market=market, cancel=deps.stop_event)
        except ThrottleBlocked as e:
            # 감속 중엔 분석 세션도 같은 이유로 막힌다 — 오류가 아니라 그날 분석 생략(알림 하루 1번)
            throttle_notice(deps, market, str(e))
            return {"skipped": f"사용량 감속 — {e}"}
        except Exception as e:  # noqa: BLE001 - scan 실패가 analyze 시도를 막지 않는다
            logger.exception("scan 실패 %s", market)
            scan_error = f"{type(e).__name__}: {e}"  # 알림은 analyze_body 가 원인과 함께 1건만
    finally:
        conn.close()
    out = analyze_body(deps, market, scan_error=scan_error)
    hooks.run(deps.hooks, "scan_analyze.post", deps=deps, market=market, result=out)
    return out


# --- 큐 kind ---------------------------------------------------------------------------

Market = Literal["KR", "US"]


class ScanAnalyzeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market: Market


class AnalyzeTickerParams(BaseModel):
    """단일 종목 분석 요청. dry_run 은 데몬 `--dry-run` 을 뒤집지 못한다(둘 중 하나라도 dry)."""

    model_config = ConfigDict(extra="forbid")

    market: Market
    ticker: str = Field(min_length=1, max_length=12, pattern=r"^[A-Za-z0-9.\-]+$")
    exchange: Literal["NAS", "NYS", "AMS"] | None = None
    dry_run: bool = True

    @field_validator("ticker")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()


def scan_analyze_request(deps, params: dict, progress) -> dict:
    """큐 `scan_analyze` 본문: 한 시장의 스캔 → 분석."""
    return _scan_analyze(deps, params["market"])


def analyze_ticker_request(deps, params: dict, progress) -> dict:
    """큐 `analyze_ticker` 본문: 단일 종목 분석 1회. 미국 종목은 거래소 코드가 필요하다 — 요청에
    없으면 원장(`ticker_exchanges`)에서 찾고, 없으면 오류."""
    market, ticker, exchange = params["market"], params["ticker"], params.get("exchange")
    if market == "US" and not exchange:
        conn = deps.conn_factory()
        try:
            exchange = exec_ledger.exchange_of(conn, market, ticker)
        finally:
            conn.close()
        if not exchange:
            return {"error": f"미국 종목은 거래소 코드가 필요해요: {ticker}"}
    _close_stale(deps)
    today = run_date_for(market, deps.now())
    return analyze_targets(
        deps,
        market,
        [{"ticker": ticker, "exchange": exchange}],
        today=today,
        dry_run=bool(params.get("dry_run", True)),
    )


SCAN_ANALYZE = JobSpec(
    id="{market}_scan_analyze", kind="scan_analyze", trigger="cron", per_market=True,
    schedule_key="scan_analyze.{market}", interval_s=None, enabled=None, gate="market",
    heavy=True, misfire="scan", catch_up="queue", start_soon_s=0, body=scan_analyze_body,
    days="mon-fri",
)  # fmt: skip

QUEUE_KINDS_SCAN = (
    QueueKind(
        "scan_analyze", ScanAnalyzeParams, "스캔 → 분석", "slow", 0, True, False,
        scan_analyze_request,
    ),
    QueueKind(
        "analyze_ticker", AnalyzeTickerParams, "종목 분석", "slow", 0, True, False,
        analyze_ticker_request,
    ),
)  # fmt: skip
