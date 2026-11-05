"""LLM 세션이 쓰는 spot 도구 구현 (status·checkpoint list/read/write/bundle/summarize·재시세·
submit-decision).

모든 도구는 예외 대신 {"ok": bool, ...} dict 를 돌려준다(CLI 가 JSON 1줄로 그대로 출력).
순서 강제는 프롬프트가 아니라 `StageRule` 전제조건이 맡는다(`kernel.session.stages.check_rule`).
submit-decision 은 `SUBMIT_RULE` 을 본 뒤 결정을 저장하고 `executor.execute` 로 계좌별 실행을
넘긴다.
"""

from __future__ import annotations

import functools
import logging
import os
import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from trader.features.spot import costs, executor, rules, schemas, tradability
from trader.features.spot import ledger as spot_ledger
from trader.features.spot import summary as summary_mod
from trader.kernel.execution import ledger as exec_ledger
from trader.kernel.notify import NullNotifier
from trader.kernel.platform import paths
from trader.kernel.registry.types import StageRule
from trader.kernel.session import calls, runner, tooling
from trader.kernel.session.run_store import checkpoint_payload as _wrap
from trader.kernel.session.run_store import (
    list_checkpoints,
    market_dir,
    read_checkpoint,
    run_date_for,
    run_dir,
    write_checkpoint,
)
from trader.kernel.session.stages import check_rule, first_rule

logger = logging.getLogger("trader.spot")

MIN_REPORTS = 2
REQUOTE_AFTER_S = 600  # 입력 수집 뒤 이만큼 지났으면 제출 때 현재가를 다시 받는다
BUNDLE_LIMIT_BYTES = 24_000  # Claude Code Bash 출력 한도(30KB, UTF-8 바이트) 아래

_ANALYST_RE = re.compile(r"^10_analyst_([a-z_]+)$")
_DEBATE_RE = tooling.DEBATE_RE
_RESUME_RE = re.compile(r"^31_decision_r(\d+)$")
_TICKER_RE = re.compile(r"^(?!\.+$)[A-Za-z0-9.\-]{1,16}$")  # 점만 있는 값(., ..)은 거부
_STAGE_RE = re.compile(r"^\d{2}_[a-z0-9_\-]{1,40}$")

# 공용 헬퍼는 kernel/session/tooling 이 정본 — 옛 밑줄 이름 호환 별칭
_unescape = tooling.unescape
_call = tooling.call_meta
_reports = tooling.reports
_shrink = tooling.shrink
_trader_summary = tooling.trader_summary
evidence_stats = tooling.evidence_stats
EVIDENCE_ROLES = tooling.EVIDENCE_ROLES
EVIDENCE_DEFINITION = tooling.EVIDENCE_DEFINITION


def _load_turns(d: Path) -> dict[int, dict[str, schemas.DebateTurn]]:
    """`20_debate_r<n>_<side>` 토론 턴을 {round: {side: DebateTurn}} 로."""
    return tooling.load_turns(d, _DEBATE_RE)


# LLM 이 checkpoint write 로 쓸 수 있는 단계와 전제조건. 패턴은 fnmatch glob 이라 역할·방향의
# 유효성은 `schemas.schema_for` 와 파일명↔페이로드 일치 검사가 맞춘다. 30_decision 은 submit 전용.
WRITE_RULES = (
    StageRule("10_analyst_*", requires=("00_inputs",)),
    StageRule("20_debate_r*_*", requires=("00_inputs",), min_counts=(("10_analyst_*", 2),)),
)
SUBMIT_RULE = StageRule(
    "30_decision",
    requires=("00_inputs", "25_research_summary"),
    min_counts=(("10_analyst_*", 2),),
)


class ToolInputError(ValueError):
    """ticker·market·stage 형식 오류 (경로 탈출 방지)."""


class ToolError(Exception):
    """도구 거부 (파이썬 호출자가 예외로 받고 싶을 때)."""


def validate_ids(ticker: str, market: str, stage: str | None = None) -> None:
    """ticker·market·stage 는 경로로 쓰이므로 형식을 검증한다."""
    if not _TICKER_RE.match(ticker or ""):
        raise ToolInputError(f"ticker 형식 오류: {ticker!r}")
    if market not in ("KR", "US"):
        raise ToolInputError(f"market 은 KR 또는 US: {market!r}")
    if stage is not None and not _STAGE_RE.match(stage):
        raise ToolInputError(f"stage 형식 오류: {stage!r}")


_validate_ids = validate_ids  # 옛 이름 호환


def _guarded(fn):
    """ToolInputError 를 {"ok": false} 로 바꾸는 데코레이터."""

    @functools.wraps(fn)
    def wrapper(ctx, *, ticker=None, market=None, stage=None, **kw):
        try:
            validate_ids(ticker, market, stage)
        except ToolInputError as e:
            return {"ok": False, "error": str(e)}
        kwargs = {"ticker": ticker, "market": market, **kw}
        if stage is not None:
            kwargs["stage"] = stage
        return fn(ctx, **kwargs)

    return wrapper


@dataclass
class ToolContext:
    """도구 서브프로세스의 실행 컨텍스트. policy·paused·inputs 는 accounts·inputs 포트에서 온다.
    `ports` 는 `Boot.ports` 그대로 — 실행 게이트·trader_context 가 `regime`·`scan` 을 본다."""

    settings: Any
    accounts: list
    conn: sqlite3.Connection | None = None
    state: Path | None = None
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    brokers: dict[str, Any] = field(default_factory=dict)
    notifier: Any = None
    dry_run: bool = True
    holiday_fn: Callable[[str, date], bool] | None = None  # (시장, 날짜) → 휴장 여부
    market_client: Any | None = None
    policy: Callable[[Any, Any], tuple[str, str | None]] | None = None
    paused: Callable[[Any], bool] | None = None
    inputs: Any = None  # InputsPort 구현
    ports: Mapping[str, Any] = field(default_factory=dict)


def ctx_from_ports(
    *,
    settings: Any,
    conn: sqlite3.Connection | None,
    state: Path | None,
    ports: Any,
    notifier: Any,
    dry_run: bool,
    now: Callable[[], datetime] | None = None,
) -> ToolContext:
    """accounts·inputs 포트로 ToolContext 를 조립한다(CLI `tool_ctx` 와 FAKE 세션이 공유)."""
    acc = ports.get("accounts")
    brokers: dict[str, Any] = {}
    accounts: list = []
    market_client = holiday_fn = policy = paused = None
    if acc is not None:
        accounts = list(acc.accounts())
        brokers = {b.account.id: b for b in acc.brokers()}
        market_client = acc.market_client()
        holiday_fn = acc.holiday_fn()
        policy, paused = acc.policy, acc.paused
    extra = {"now": now} if now is not None else {}
    return ToolContext(
        settings=settings,
        accounts=accounts,
        conn=conn,
        state=state,
        brokers=brokers,
        notifier=notifier,
        dry_run=dry_run,
        holiday_fn=holiday_fn,
        market_client=market_client,
        policy=policy,
        paused=paused,
        inputs=ports.get("inputs"),
        ports=dict(ports),
        **extra,
    )


def tool_ctx(boot: Any) -> ToolContext:
    """부트 결과(`ports=True`)로 ToolContext 를 만든다. `TRADER_DRY_RUN` 이 없거나 "1" 이면
    dry_run(주문·승인 요청 없음) — LLM 이 플래그를 빠뜨려도 주문이 나가지 않는다."""
    return ctx_from_ports(
        settings=boot.settings,
        conn=boot.conn,
        state=paths.state_dir(),
        ports=boot.ports,
        notifier=executor.boot_notifier(boot),
        dry_run=os.environ.get("TRADER_DRY_RUN", "1") != "0",
    )


def _dir(ctx: ToolContext, ticker: str, market: str, d: date) -> Path:
    return run_dir(d, market, ticker, ctx.state)


# --- checkpoint list ------------------------------------------------------------


@_guarded
def checkpoint_list(ctx: ToolContext, *, ticker: str, market: str, date: date) -> dict:
    return {"ok": True, "stages": list_checkpoints(_dir(ctx, ticker, market, date))}


# --- 트레이더 컨텍스트 ------------------------------------------------------------


def _mode_of(ctx: ToolContext, account: Any) -> str | None:
    if ctx.policy is not None and ctx.conn is not None:
        return ctx.policy(ctx.conn, account)[0]
    return getattr(account, "mode", None)


def portfolio_context(ctx: ToolContext, market: str, d: date) -> dict | None:
    """계좌별 {env, mode, date, nav, cash, positions} (최신 스냅샷). 원장이 없으면 None.
    스냅샷은 마지막 관측값이라 `d` 로 자르지 않는다(US 운영일과 스냅샷 날짜가 어긋날 수 있다)."""
    if ctx.conn is None:
        return None
    out: dict[str, dict] = {}
    for a in ctx.accounts:
        if market not in a.markets:
            continue
        snap = exec_ledger.latest_positions(ctx.conn, a.id, market) or {}
        out[a.id] = {
            "env": a.env,
            "mode": _mode_of(ctx, a),
            "date": snap.get("date"),
            "nav": snap.get("nav"),
            "cash": snap.get("cash"),
            "positions": snap.get("positions") or [],
        }
    return out


def _holidays_between(ctx: ToolContext, market: str, start: date, end: date) -> set[date]:
    if ctx.holiday_fn is None:
        return set()
    from datetime import timedelta

    span = (start + timedelta(days=i) for i in range((end - start).days + 1))
    return {day for day in span if ctx.holiday_fn(market, day)}


def holding_context(ctx: ToolContext, ticker: str, market: str, d: date) -> dict:
    """계좌별 이 종목의 보유 거래일·최소 보유 충족 여부(익절가 도달이 최소 보유 전이면 SELL/TRIM 이
    게이트에서 거부될 수 있다). 보유한 계좌만."""
    out: dict[str, dict] = {}
    if ctx.conn is None:
        return out
    for a in ctx.accounts:
        if market not in a.markets:
            continue
        since = exec_ledger.held_since(ctx.conn, a.id, [ticker]).get(ticker)
        if since is None:
            continue
        days = rules._trading_days_between(since, d, _holidays_between(ctx, market, since, d))
        out[a.id] = {
            "held_since": since.isoformat(),
            "held_trading_days": days,
            "min_hold_days": rules.MIN_HOLD_TRADING_DAYS,
            "sell_allowed": days >= rules.MIN_HOLD_TRADING_DAYS,
        }
    return out


def _regime_port(ctx: ToolContext) -> Any | None:
    """원장이 있고 헤지가 켜져 있을 때의 regime 포트(아니면 None)."""
    port = ctx.ports.get("regime")
    if ctx.conn is None or port is None or not port.hedge_cfg().enabled:
        return None
    return port


def _market_view_context(ctx: ToolContext, market: str) -> dict | None:
    """오늘 시장 세션의 결론 요약 (Phase 18). 없으면 None."""
    port = _regime_port(ctx)
    if port is None:
        return None
    v = port.view_today(ctx.conn, market, run_date_for(market, ctx.now()))
    if not v:
        return None
    return {k: v.get(k) for k in ("stance", "hedge_weight", "leveraged", "confidence", "rationale")}


def _regime_context(ctx: ToolContext, market: str) -> dict | None:
    """국면(Phase 17 H): {state, count, signals, since}. 기록이 없거나 원장·포트가 없으면 None."""
    port = _regime_port(ctx)
    if port is None:
        return None
    r = port.load(ctx.conn, market)
    if not r:
        return None
    sig = r.get("signals") or {}
    return {
        "state": r.get("state"),
        "count": r.get("count"),
        "signals": {k: sig.get(k) for k in ("ma_below", "momentum_weak", "vol_high")},
        "since": r.get("since"),
        "note": "bear: 게이트가 신규 BUY/ADD 거부·헤지 ETF 보유; neutral: 한도 축소",
    }


def trader_context(ctx: ToolContext, market: str, d: date, ticker: str, inputs: dict) -> dict:
    """트레이더 입력의 파이썬 제공분: quote·risk_limits·costs_bp·portfolio·holding·regime·
    market_view(`ports["regime"]` 가 없으면 둘 다 None)·quant(`05_quant` 체크포인트, 없으면 None)·
    memory·memory_status(`ports["evaluation"]` 의 유사 과거 판단, 포트·원장이 없으면 빈 목록·
    warmup)·calibration(`35_calibration` 체크포인트, 주입이 꺼져 있으면 None). (theme 은 이연 —
    README 알려진 차이)"""
    quote = ((inputs.get("technical") or {}).get("quote") or {}).get("data")
    return {
        "quote": quote,
        "risk_limits": ctx.settings.risk.model_dump(),
        "costs_bp": costs.round_trip_bp(market),
        "portfolio": portfolio_context(ctx, market, d),
        "holding": holding_context(ctx, ticker, market, d),
        "regime": _regime_context(ctx, market),
        "market_view": _market_view_context(ctx, market),
        "quant": _quant_context(ctx, market, d, ticker),
        **_memory_context(ctx, inputs, market, d),
        "calibration": _calibration_context(ctx, market, d, ticker),
    }


def _memory_context(ctx: ToolContext, inputs: dict, market: str, d: date) -> dict:
    """{memory, memory_status} — `ports["evaluation"]` 가 없거나 원장이 없으면 빈 목록·warmup."""
    ev = ctx.ports.get("evaluation")
    if ev is None or ctx.conn is None:
        return {"memory": [], "memory_status": {"warmup": True, "breaker": None}}
    out = ev.memory_context(
        ctx.conn, inputs=inputs, market=market, asof_date=d, now=ctx.now(), state=ctx.state
    )
    return {"memory": out.get("memory", []), "memory_status": out.get("memory_status")}


def _calibration_context(ctx: ToolContext, market: str, d: date, ticker: str) -> dict | None:
    """`35_calibration` 체크포인트의 data. 포트가 없거나 주입이 꺼져 있으면 None."""
    ev = ctx.ports.get("evaluation")
    if ev is None or not ev.calibration_injected():
        return None
    saved = read_checkpoint(_dir(ctx, ticker, market, d), "35_calibration")
    return saved["data"] if saved is not None else None


def _quant_context(ctx: ToolContext, market: str, d: date, ticker: str) -> dict | None:
    """`05_quant` 체크포인트의 data. 없으면 None."""
    saved = read_checkpoint(_dir(ctx, ticker, market, d), "05_quant")
    return saved["data"] if saved is not None else None


def ensure_quant(ctx: ToolContext, *, ticker: str, market: str, date: date) -> dict | None:
    """승격된 퀀트 모델·피처 행이 있으면 05_quant 를 쓴다(이미 있으면 그대로). 없으면 None.
    모델·피처는 `ports["ml"]` 로 얻는다 — 포트가 없거나 퀀트가 꺼져 있으면 조용히 None."""
    d = _dir(ctx, ticker, market, date)
    existing = read_checkpoint(d, "05_quant")
    if existing is not None:
        return existing.get("data")
    ml = ctx.ports.get("ml")
    if ml is None:
        return None
    try:
        if not ml.quant_enabled():
            return None
        # 그 시장 모델 우선·전시장 폴백, 다른 시장 한정 모델은 쓰지 않는다(registry.latest 계약)
        found = ml.latest_model(market)
    except Exception as e:  # noqa: BLE001 - ML 은 선택 입력: 어떤 오류도 수집·분석을 막지 않는다
        logger.warning("05_quant 생략(레지스트리 오류): %s", e)
        return None
    if found is None:
        return None
    model, meta = found
    try:
        row = ml.latest_row(ticker, market, asof=date)
    except Exception as e:  # noqa: BLE001
        logger.warning("05_quant 생략(피처 읽기 실패) %s %s: %s", market, ticker, e)
        return None
    if row is None:
        logger.info("05_quant 생략 %s %s: 피처 행 없음", market, ticker)
        return None
    try:
        data = ml.build_report(model, meta, row, ticker=ticker, asof=ctx.now().isoformat())
    except Exception as e:  # noqa: BLE001 - 모델 오류는 분석을 막지 않는다
        logger.warning("05_quant 생성 실패 %s %s: %s", market, ticker, e)
        return None
    write_checkpoint(d, "05_quant", _wrap(data, _call("quant", {"model": data["model"]})))
    return data


def ensure_calibration(ctx: ToolContext, *, ticker: str, market: str, date: date) -> dict | None:
    """보정 주입(`ml.yaml calibration.enabled ∧ inject`)이 켜졌고 결과 엔트리가 min_decisions
    이상이면 35_calibration 을 쓴다(이미 있으면 그대로). 리포트는 `ports["evaluation"]` 이 만든다
    — 포트·원장이 없거나 주입이 꺼져 있으면 None."""
    ev = ctx.ports.get("evaluation")
    if ev is None or ctx.conn is None:
        return None
    try:
        if not ev.calibration_injected():  # 주입은 사람이 켠다(결정 14번) — 기존 것도 무시
            return None
        d = _dir(ctx, ticker, market, date)
        existing = read_checkpoint(d, "35_calibration")
        if existing is not None:
            return existing.get("data")
        report = ev.calibration_report(
            ctx.conn,
            ticker=ticker,
            market=market,
            asof_date=date,
            state=ctx.state,
            now_iso=ctx.now().isoformat(),
        )
        if report is None:
            return None
        write_checkpoint(d, "35_calibration", _wrap(report, _call("calibration", None)))
    except Exception as e:  # noqa: BLE001 - 보정은 선택 입력
        logger.warning("35_calibration 생략 %s %s: %s", market, ticker, e)
        return None
    return report


# --- 절 압축 ----------------------------------------------------------------------
# 서브에이전트에 넘기는 절은 Claude Code 의 Bash 출력 한도(약 30KB, 초과 시 파일로 치환돼
# LLM 이 못 본다)보다 훨씬 작아야 한다. 지표가 250봉을 요약하므로 원시 봉은 최근 것만 준다.
COMPACT_LIMITS = {
    "daily": 30,
    "minute": 20,
    "orderbook_levels": 5,
    "news_stock": 15,
    "news_macro": 8,
    "news_summary_chars": 120,
    "posts": 20,
    "post_body_chars": 200,
    "post_title_chars": 100,
}


def _section_data(sec: Any) -> Any:
    return sec.get("data") if isinstance(sec, dict) else None


def _with_note(sec: dict, data: Any, note: str) -> dict:
    return {**sec, "data": data, "note": note}


def _trim_news(items: list | None, n: int, chars: int) -> list:
    out = []
    for it in (items or [])[:n]:
        if not isinstance(it, dict):
            continue
        row = {k: v for k, v in it.items() if k != "url"}
        if isinstance(row.get("summary"), str):
            row["summary"] = row["summary"][:chars]
        out.append(row)
    return out


def _compact_section(section: str, body: dict) -> dict:
    """역할별 절을 압축한다. 원본 체크포인트 파일은 바꾸지 않는다."""
    lim = COMPACT_LIMITS
    out = dict(body)
    if section == "technical":
        daily = _section_data(body.get("daily"))
        if isinstance(daily, list) and len(daily) > lim["daily"]:
            out["daily"] = _with_note(
                body["daily"],
                daily[-lim["daily"] :],
                f"최근 {lim['daily']}봉만 (전체 {len(daily)}봉은 indicators 에 반영됨)",
            )
        minute = _section_data(body.get("minute"))
        if isinstance(minute, list) and len(minute) > lim["minute"]:
            out["minute"] = _with_note(
                body["minute"], minute[-lim["minute"] :], f"최근 {lim['minute']}분봉만"
            )
        ob = _section_data(body.get("orderbook"))
        if isinstance(ob, dict):
            n = lim["orderbook_levels"]
            out["orderbook"] = _with_note(
                body["orderbook"],
                {**ob, "asks": (ob.get("asks") or [])[:n], "bids": (ob.get("bids") or [])[:n]},
                f"상위 {n}호가만",
            )
    elif section == "news_macro":
        news = _section_data(body.get("news"))
        if isinstance(news, dict):
            out["news"] = _with_note(
                body["news"],
                {
                    **news,
                    "stock": _trim_news(
                        news.get("stock"), lim["news_stock"], lim["news_summary_chars"]
                    ),
                    "macro": _trim_news(
                        news.get("macro"), lim["news_macro"], lim["news_summary_chars"]
                    ),
                },
                f"종목 {lim['news_stock']}건·거시 {lim['news_macro']}건, "
                f"요약 {lim['news_summary_chars']}자",
            )
    elif section == "sentiment":
        comm = _section_data(body.get("community"))
        if isinstance(comm, dict):
            posts = []
            for p in (comm.get("posts") or [])[: lim["posts"]]:
                if not isinstance(p, dict):
                    continue
                row = {k: v for k, v in p.items() if k != "url"}
                row["title"] = str(row.get("title", ""))[: lim["post_title_chars"]]
                row["body"] = str(row.get("body", ""))[: lim["post_body_chars"]]
                posts.append(row)
            out["community"] = _with_note(
                body["community"], {**comm, "posts": posts}, f"최근 {lim['posts']}건만"
            )
    return out


# --- checkpoint read ----------------------------------------------------------------


@_guarded
def checkpoint_read(
    ctx: ToolContext,
    *,
    ticker: str,
    market: str,
    date: date,
    stage: str,
    section: str | None = None,
) -> dict:
    """절(section)을 지정하면 서브에이전트용으로 압축해 돌려준다. 전체 읽기는 파일 그대로."""
    data = read_checkpoint(_dir(ctx, ticker, market, date), stage)
    if data is None:
        return {"ok": False, "error": f"체크포인트 없음: {stage}"}
    if section is None:
        return {"ok": True, "data": data}
    if stage == "00_inputs" and section == "trader_context":
        return {"ok": True, "data": trader_context(ctx, market, date, ticker, data)}
    body = data.get("data", data) if stage != "00_inputs" else data
    if section not in body:
        return {"ok": False, "error": f"절 없음: {section}"}
    if stage != "00_inputs":
        return {"ok": True, "data": {section: body[section]}}
    out = body[section]
    if isinstance(out, dict):
        out = _compact_section(section, out)
        errors = [e for e in data.get("errors", []) if e.startswith(section + ".")]
        out = {**out, "input_errors": errors, "meta": data.get("meta")}
        if section in ("fundamental", "sentiment"):  # 에이전트 md: "함께 오는 quote 는 현재가다"
            out["quote"] = (body.get("technical") or {}).get("quote")
    return {"ok": True, "data": out}


# --- checkpoint write ---------------------------------------------------------------

_UNKNOWN_STAGE = "알 수 없는 단계이거나 LLM 이 쓰는 단계가 아닙니다"


@_guarded
def checkpoint_write(
    ctx: ToolContext,
    *,
    ticker: str,
    market: str,
    date: date,
    stage: str,
    payload: dict,
    agent: str | None = None,
    call_meta: dict | None = None,
    model: str | None = None,
) -> dict:
    """LLM 이 쓸 수 있는 단계(`WRITE_RULES`)만 받는다: 스키마·파일명↔페이로드 일치·라운드 상한
    검증 뒤 전제조건(선행 단계)을 본다."""
    if model:
        call_meta = {**(call_meta or {}), "model": model}
    if stage.startswith("30_decision"):
        return {"ok": False, "error": "30_decision 은 submit-decision 도구로만 제출할 수 있습니다"}
    rule = first_rule(WRITE_RULES, stage)
    schema = schemas.schema_for(stage)
    if rule is None or schema is None:
        return {"ok": False, "error": f"{_UNKNOWN_STAGE}: {stage}"}
    try:
        validated = schema.model_validate(_unescape(payload))
    except ValidationError as e:
        msgs = "; ".join(
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()
        )
        return {"ok": False, "error": f"스키마 검증 실패({stage}): {msgs}"}
    mismatch = _name_payload_mismatch(ctx, stage, ticker, validated)
    if mismatch:
        return {"ok": False, "error": mismatch}
    d = _dir(ctx, ticker, market, date)
    missing = check_rule(rule, stage, list_checkpoints(d))
    if missing:
        return {"ok": False, "error": f"전제조건 미충족({stage}): {'; '.join(missing)}"}
    path = write_checkpoint(d, stage, _wrap(validated.model_dump(), _call(agent, call_meta)))
    return {"ok": True, "path": str(path)}


def _name_payload_mismatch(ctx: ToolContext, stage: str, ticker: str, v: Any) -> str | None:
    m = _ANALYST_RE.match(stage)
    if m:
        role = m.group(1)
        if role not in schemas.ANALYST_ROLES:
            return f"{_UNKNOWN_STAGE}: {stage}"
        if v.role != role:
            return f"파일명 role({role}) 과 페이로드 role({v.role}) 불일치"
        if v.ticker != ticker:
            return f"ticker 불일치: {v.ticker} ≠ {ticker}"
        return None
    m = _DEBATE_RE.match(stage)
    if m:
        rnd, side = int(m.group(1)), m.group(2)
        if v.side != side or v.round != rnd:
            return f"파일명 r{rnd}_{side} 와 페이로드 round={v.round} side={v.side} 불일치"
        if rnd > ctx.settings.debate_rounds:
            return f"라운드 {rnd} 는 설정(debate_rounds={ctx.settings.debate_rounds})을 넘습니다"
    return None


# --- summarize ----------------------------------------------------------------------


@_guarded
def checkpoint_summarize(ctx: ToolContext, *, ticker: str, market: str, date: date) -> dict:
    """20_debate_* 를 모아 25_research_summary 를 만든다 (파이썬이 수렴 감시).

    토론 턴이 하나도 없으면 애널리스트 합의(`summary.analyst_consensus`)를 보고, 합의면 토론 없이
    `skipped_debate: true` 요약을, 아니면 `debate_required: true` 오류를 돌려준다.
    토론 턴이 있으면 합의 여부와 무관하게 정상 요약이다."""
    d = _dir(ctx, ticker, market, date)
    turns = _load_turns(d)
    if not turns:
        return _summarize_without_debate(ctx, d)
    if 0 not in turns or set(turns[0]) != {"bull", "bear"}:
        return {"ok": False, "error": "라운드 0 의 bull·bear 턴이 모두 필요합니다"}
    final_round = max(r for r, sides in turns.items() if set(sides) == {"bull", "bear"})
    evidence_ids = {
        str(e.get("source_id"))
        for r in _reports(d)
        for e in (r.get("evidence") or [])
        if isinstance(e, dict) and e.get("source_id")
    }
    try:
        summ = summary_mod.research_summary(
            turns[0], turns[final_round], rounds=final_round, evidence_ids=evidence_ids
        )
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    path = write_checkpoint(d, "25_research_summary", _wrap(summ, None))
    return {
        "ok": True,
        "path": str(path),
        "sycophantic": summ["sycophantic"],
        "preferred": summ["preferred"],
    }


def _summarize_without_debate(ctx: ToolContext, d: Path) -> dict:
    cfg = ctx.settings.debate
    consensus = None
    if cfg.skip_on_consensus:
        reports = [schemas.AnalystReport.model_validate(r) for r in _reports(d)]
        consensus = summary_mod.analyst_consensus(reports, cfg.consensus_min_conviction)
    if consensus is None:
        return {
            "ok": False,
            "debate_required": True,
            "error": "애널리스트 합의가 아니라 토론이 필요합니다 (라운드 0 의 bull·bear 턴 먼저)",
        }
    summ = summary_mod.skipped_summary(consensus)
    path = write_checkpoint(d, "25_research_summary", _wrap(summ, None))
    return {"ok": True, "path": str(path), "skipped_debate": True, "reason": summ["reason"]}


# --- bundle: 서브에이전트 입력 묶음 (파이썬이 조립 → 오케스트레이터는 전문 전달만) ---------

def _latest_decision_stage(d: Path) -> str | None:
    """오늘의 최신 결정 단계 (31_decision_r<n> 중 n 최대, 없으면 30_decision, 둘 다 없으면 None)."""
    stages = list_checkpoints(d)
    rounds = [int(m.group(1)) for s in stages if (m := _RESUME_RE.match(s))]
    if rounds:
        return f"31_decision_r{max(rounds)}"
    return "30_decision" if "30_decision" in stages else None


@_guarded
def checkpoint_bundle(
    ctx: ToolContext,
    *,
    ticker: str,
    market: str,
    date: date,
    kind: str,
    round: int | None = None,
    side: str | None = None,
) -> dict:
    """kind=researcher(round, side) | trader. 서브에이전트 프롬프트에 그대로 붙일 입력 묶음."""
    d = _dir(ctx, ticker, market, date)
    reports = _reports(d)
    if len(reports) < MIN_REPORTS:
        return {"ok": False, "error": f"10_analyst_* 가 {len(reports)}개 (최소 {MIN_REPORTS})"}
    if kind == "researcher":
        if round is None or side not in ("bull", "bear"):
            return {
                "ok": False,
                "error": "researcher 묶음은 --round 와 --side(bull|bear) 가 필요합니다",
            }
        turns = _load_turns(d)
        own = opp = None
        if round > 0:
            prev = turns.get(round - 1, {})
            own = prev.get(side)
            opp = prev.get("bear" if side == "bull" else "bull")
            if own is None or opp is None:
                return {"ok": False, "error": f"라운드 {round - 1} 의 양측 턴이 모두 필요합니다"}
        data = {
            "ticker": ticker,
            "round": round,
            "side": side,
            "reports": reports,
            "own_previous": own.model_dump() if own else None,
            "opponent_previous": opp.model_dump() if opp else None,
        }
        return {"ok": True, "data": _shrink(data, BUNDLE_LIMIT_BYTES)}
    if kind == "trader":
        summ = read_checkpoint(d, "25_research_summary")
        if summ is None:
            return {"ok": False, "error": "25_research_summary 없음 (summarize 먼저)"}
        inputs = read_checkpoint(d, "00_inputs") or {}
        prev_stage = _latest_decision_stage(d)
        prev = (read_checkpoint(d, prev_stage) or {}).get("data") if prev_stage else None
        data = {
            "ticker": ticker,
            "reports": reports,
            "previous_decision": prev,  # 재평가(resume) 때 트레이더가 전제로 삼는 원래·최신 결정
            "previous_decision_stage": prev_stage,
            "research_summary": _trader_summary(summ.get("data", summ)),
            "context": trader_context(ctx, market, date, ticker, inputs),
        }
        data["context"]["evidence_stats"] = evidence_stats(reports)  # evidence 를 떼기 전에 센다
        return {"ok": True, "data": _shrink(data, BUNDLE_LIMIT_BYTES, drop_evidence=True)}
    return {"ok": False, "error": f"알 수 없는 kind: {kind} (researcher|trader)"}


# --- 재시세·매매 제한 ---------------------------------------------------------------


def quote_price(inputs: dict) -> Decimal | None:
    """00_inputs 시세의 현재가. 없으면 None."""
    try:
        return Decimal(str(inputs["technical"]["quote"]["data"]["price"]))
    except (KeyError, TypeError, InvalidOperation):
        return None


def _input_change_pct(inputs: dict) -> float | None:
    """00_inputs 시세의 당일 등락률(소수). 없으면 None."""
    try:
        v = inputs["technical"]["quote"]["data"].get("change_pct")
        return float(v) if v is not None else None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _trigger_change_pct(inputs: dict, price: Decimal, run_date: date) -> float | None:
    """트리거 가격의 당일 등락률(소수): `00_inputs.technical.daily` 에서 운영일 전 마지막 일봉
    종가 대비. 직전 종가를 모르면 None — 아침 시세의 등락률로 트리거 가격을 판정하지 않는다
    (PR #26 리뷰: 아침 +12% 가 장중 되돌린 가격의 재평가를 막지 않게)."""
    try:
        rows = inputs["technical"]["daily"]["data"] or []
        prev = [r for r in rows if str(r.get("date") or "") < run_date.isoformat()]
        close = Decimal(str(prev[-1]["close"]))
        if close > 0:
            return float(price / close - 1)
    except (KeyError, TypeError, ValueError, IndexError, InvalidOperation, AttributeError):
        pass
    return None


def requote(
    ctx: ToolContext,
    market: str,
    ticker: str,
    exchange: str | None,
    inputs: dict,
    now: datetime,
) -> tuple[Decimal | None, str | None, float | None]:
    """제출 시 재시세: `00_inputs.meta.asof` 가 REQUOTE_AFTER_S 넘게 늙었고 시세 클라이언트가 있으면
    현재가를 다시 받는다. 반환 (가격, note, 당일 등락률): 가격은 입력가(재시세로 달라졌으면 새
    값, 입력에 가격이 없으면 None), note 는 판정에 남길 "재시세 71500→71800"(변동 없음·실패면
    None), 등락률은 재시세 값 우선. 실패·0·변동 없음은 입력가를 유지한다."""
    price = quote_price(inputs)
    base_pct = _input_change_pct(inputs)
    if ctx.market_client is None or price is None:
        return price, None, base_pct
    asof_raw = (inputs.get("meta") or {}).get("asof")
    try:
        asof = datetime.fromisoformat(str(asof_raw))
        if asof.tzinfo is None:
            asof = asof.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return price, None, base_pct
    if (now - asof).total_seconds() < REQUOTE_AFTER_S:
        return price, None, base_pct
    try:
        if market == "KR":
            q = ctx.market_client.get_price_kr(ticker)
        else:
            exch = exchange or (inputs.get("meta") or {}).get("exchange") or "NAS"
            q = ctx.market_client.get_price_us(ticker, exch)
        fresh = Decimal(str(getattr(q, "price", None) or 0))
        fresh_pct = getattr(q, "change_pct", None)
    except Exception as e:  # noqa: BLE001 - 재시세 실패는 입력가로 간다
        logger.warning("재시세 실패 %s %s: %s", market, ticker, e)
        return price, None, base_pct
    pct = float(fresh_pct) if fresh_pct is not None else base_pct
    if fresh <= 0 or fresh == price:
        return price, None, pct if fresh > 0 else base_pct
    note = f"재시세 {price}→{fresh}"
    logger.info("%s %s %s (입력 %s)", market, ticker, note, asof_raw)
    return fresh, note, pct


def trade_block(inputs: dict) -> str | None:
    """00_inputs 시세의 종목상태·시장경고·임시정지로 본 매매 제한 사유."""
    try:
        return tradability.block_reason(inputs["technical"]["quote"]["data"])
    except (KeyError, TypeError):
        return None


# --- submit-decision ------------------------------------------------------------------


def _ingest_run(ctx: ToolContext, d: Path, day: date, market: str, ticker: str) -> None:
    """리포트·토론·결정 체크포인트를 원장 테이블에 멱등 적재한다 (메트릭·UI 용)."""
    if ctx.conn is None:
        return
    for stage in list_checkpoints(d):
        cp = read_checkpoint(d, stage)
        if isinstance(cp, dict):
            spot_ledger.ingest_checkpoint(ctx.conn, day, market, ticker, stage, cp)


def _account_finished(
    ctx: ToolContext, d: Path, risk_stage: str, stages: list[str], day: date, market: str,
    ticker: str,
) -> bool:  # fmt: skip
    """재개 판단: 이 계좌의 실행이 끝났는가. 판정(40_)만 있고 **통과했는데 주문·승인 흔적이 없으면**
    (판정 저장 뒤 주문 전에 끊김) 끝나지 않은 것으로 본다. 재실행은 executor 의 살아 있는 주문·
    승인 중복 방지와 실행 키가 지킨다. dry-run 판정은 지금 실전이면 실행이 아니다."""
    v = (read_checkpoint(d, risk_stage) or {}).get("data") or {}
    if v.get("dry_run") and not ctx.dry_run:
        return False
    if not v.get("approved") or v.get("dry_run") or v.get("mode") == "off":
        return True
    acct = risk_stage[len("40_risk_check_") :]
    if f"50_order_{acct}" in stages:
        return True
    if v.get("mode") == "approve" and ctx.conn is not None:
        did = spot_ledger.decision_id(ctx.conn, day, market, ticker)
        row = ctx.conn.execute(
            "SELECT 1 FROM approvals WHERE decision_id=? AND account_id=? LIMIT 1", (did, acct)
        ).fetchone()
        return row is not None
    return False


def _exec_ctx_of(ctx: ToolContext) -> executor.ExecContext:
    """도구 컨텍스트 → 실행 컨텍스트. FAKE 세션은 가짜 브로커 잔고를 기록하지 않는다."""
    assert ctx.conn is not None
    policy, paused = executor.policy_pair(ctx.policy, ctx.paused)
    return executor.ExecContext(
        settings=ctx.settings,
        accounts=ctx.accounts,
        conn=ctx.conn,
        brokers=ctx.brokers,
        policy=policy,
        paused=paused,
        notifier=ctx.notifier or NullNotifier(),
        state=ctx.state,
        now=ctx.now,
        holiday_fn=ctx.holiday_fn,
        record_portfolio=not runner.is_fake(),
        market_client=ctx.market_client,
        ports=ctx.ports,
    )


def _execute(
    ctx: ToolContext,
    d: date,
    market: str,
    decision: schemas.Decision,
    inputs: dict,
    price: Decimal,
    *,
    resume_round: int | None = None,
    skip_accounts: frozenset[str] = frozenset(),
    extra_note: str | None = None,
    change_pct: float | None = None,
) -> list[dict]:
    """리스크 게이트·계좌별 실행(주문은 executor → 커널 코어 안에서만). 결정은 1회, 판정·주문은
    계좌마다. 재평가 라운드는 41_/51_ 체크포인트 이름에 들어간다. 메모리 기록은 호출자가
`_record_memory` 로 한다."""
    return executor.execute(
        _exec_ctx_of(ctx),
        decision,
        date=d,
        market=market,
        price=price,
        exchange=(inputs.get("meta") or {}).get("exchange"),
        dry_run=ctx.dry_run,
        resume_round=resume_round,
        skip_accounts=skip_accounts,
        extra_note=extra_note,
        change_pct=change_pct,
        trade_block=trade_block(inputs),
    )


def _record_memory(
    ctx: ToolContext,
    decision: schemas.Decision,
    market: str,
    d: date,
    *,
    round_no: int,
    skipped_debate: bool | None,
    now: datetime | None = None,
) -> int | None:
    """결정 1건(라운드별)을 `ports["evaluation"]` 의 메모리 엔트리로 기록한다. 포트·원장이 없으면
    None. `now` 는 실행 직전 시각(그 뒤의 주문이 체결가 채점 창이 된다) — 없으면 지금."""
    ev = ctx.ports.get("evaluation")
    if ev is None or ctx.conn is None:
        return None
    return ev.record_decision(
        ctx.conn,
        decision=decision.model_dump(),
        ticker=decision.ticker,
        market=market,
        date=d,
        round_no=round_no,
        now=now or ctx.now(),
        state=ctx.state,
        accounts=ctx.accounts,
        skipped_debate=skipped_debate,
    )


def _resume_saved(
    ctx: ToolContext, d: Path, stages: list[str], day: date, market: str, ticker: str
) -> dict:
    """같은 날 재제출(세션 재시도·재실행): 기존 결정을 돌려주고 실행을 반복하지 않는다(멱등).
    단 판정(40_risk_check_*)이 끝나지 않은 계좌가 있으면(저장 뒤 execute 가 끊김) 그 계좌만
    실행을 재개한다(`resumed_execution`)."""
    existing = read_checkpoint(d, "30_decision") or {}
    out: dict[str, Any] = {
        "ok": True,
        "already": True,
        "path": str(d / "30_decision.json"),
        "action": (existing.get("data") or {}).get("action"),
        "message": "30_decision 이 이미 있어 기존 결정을 유지함",
    }
    executed = {
        s[len("40_risk_check_") :]
        for s in stages
        if s.startswith("40_risk_check_")
        and _account_finished(ctx, d, s, stages, day, market, ticker)
    }
    wanted = {a.id for a in ctx.accounts if market in a.markets}
    if executed and not (wanted - executed):
        return out
    try:
        decision = schemas.Decision.model_validate(existing.get("data") or {})
    except ValidationError as e:
        return out | {
            "message": f"기존 30_decision 을 해석할 수 없어 실행 생략: 오류 {e.error_count()}건"
        }
    inputs = read_checkpoint(d, "00_inputs") or {}
    price, note, change_pct = requote(
        ctx, market, ticker, (inputs.get("meta") or {}).get("exchange"), inputs, ctx.now()
    )
    if price is None:
        return out | {"message": "00_inputs 에 현재가가 없어 실행을 재개하지 못함"}
    if decision.stop_loss is not None and Decimal(str(decision.stop_loss)) >= price:
        why = f"손절선 {decision.stop_loss} 이 재시세 {price} 이상이라 실행 생략"
        return out | {"message": why}
    _ingest_run(ctx, d, day, market, ticker)
    decided_at = ctx.now()  # 메모리 decided_at 은 실행 직전 시각
    executions = _execute(
        ctx, day, market, decision, inputs, price,
        skip_accounts=frozenset(executed), extra_note=note, change_pct=change_pct,
    )  # fmt: skip
    memory_entry_id = _record_memory(
        ctx, decision, market, day, round_no=0, skipped_debate=None, now=decided_at
    )
    logger.warning("미실행 결정의 실행 재개 %s %s: %s", market, ticker, decision.action)
    return out | {
        "resumed_execution": True,
        "message": "30_decision 은 있었지만 판정이 없어 실행을 재개함",
        "executions": executions,
        "memory_entry_id": memory_entry_id,
    }


@_guarded
def submit_decision(
    ctx: ToolContext,
    *,
    ticker: str,
    market: str,
    date: date,
    payload: dict,
    agent: str = "trader",
    call_meta: dict | None = None,
    stage: str | None = None,
) -> dict:
    """결정 제출: 전제조건(`SUBMIT_RULE`) → 스키마 → ticker 일치 → 현재가(재시세) → 손절선 검사 →
    `30_decision` 저장 → 원장 적재 → 계좌별 실행. stage 가 `31_decision_r<n>` 이면 재평가
    (30_decision 이 있어야 함). 재평가의 현재가·손절·추격 게이트 가격은 오늘 가장 최근
    트리거 가격(`latest_trigger_price`)이고, 가격 없는 트리거(뉴스)면 입력가 + 재시세다.

    같은 날 `30_decision` 이 이미 있으면 기존 결정을 돌려주고(`already`), 실행이 끝나지 않은 계좌만
    재개한다. 원장 연결이 없으면 `30_decision` 을 쓰기 전에 거부한다(재시도가 멱등 분기에 걸리지
    않게). 반환 {ok, path, stage, action, price, requote, dry_run, executions, memory_entry_id,
    resumed_execution?, already?} — `memory_entry_id` 는 `ports["evaluation"]` 메모리 엔트리(없으면
    None)."""
    d = _dir(ctx, ticker, market, date)
    stages = list_checkpoints(d)
    resume_stage = None
    if stage is not None and stage != "30_decision":
        if not _RESUME_RE.match(stage):
            return {"ok": False, "error": f"stage 는 30_decision 또는 31_decision_r<n>: {stage!r}"}
        if "30_decision" not in stages:
            return {"ok": False, "error": "재평가는 30_decision 이 있어야 합니다"}
        if stage in stages:
            return {"ok": False, "error": f"{stage} 이 이미 있습니다 (다음 번호를 쓰세요)"}
        resume_stage = stage
    if ctx.conn is None:
        return {"ok": False, "error": "원장 연결이 없어 계좌별 실행을 할 수 없음"}
    if resume_stage is None and "30_decision" in stages:
        return _resume_saved(ctx, d, stages, date, market, ticker)
    missing = check_rule(SUBMIT_RULE, "30_decision", stages)
    if missing:
        return {"ok": False, "error": "전제조건 미충족: " + ", ".join(missing)}
    try:
        decision = schemas.Decision.model_validate(_unescape(payload))
    except ValidationError as e:
        msgs = "; ".join(
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()
        )
        return {"ok": False, "error": f"스키마 검증 실패(30_decision): {msgs}"}
    if decision.ticker != ticker:
        return {"ok": False, "error": f"ticker 불일치: {decision.ticker} ≠ {ticker}"}
    inputs = read_checkpoint(d, "00_inputs") or {}
    meta = inputs.get("meta") or {}
    trig = (
        spot_ledger.latest_trigger_price(ctx.conn, market, ticker, date) if resume_stage else None
    )
    if trig is not None:
        # 재평가의 현재가는 아침 시세가 아니라 장중 트리거 가격이다(트레이더 md 의 "현재가").
        # 당일 등락률 = 트리거 가격 ÷ 직전 완성 봉 종가 − 1 — None 으로 두면 장중 급등을 쫓는
        # BUY 가 추격 금지를 지난다
        price, note, change_pct = trig, None, _trigger_change_pct(inputs, trig, date)
    else:  # 일반 제출, 그리고 가격 없는 트리거(뉴스) 재평가는 입력 시세 + 재시세
        price, note, change_pct = requote(
            ctx, market, ticker, meta.get("exchange"), inputs, ctx.now()
        )
    if price is None:
        return {
            "ok": False,
            "error": "00_inputs 의 technical.quote 에 현재가가 없어 판정할 수 없음",
        }
    if decision.stop_loss is not None and Decimal(str(decision.stop_loss)) >= price:
        # 게이트는 BUY/ADD 주문만 막지만 저장된 결정의 손절선은 monitor 가 모든 action 에 대해
        # 읽어 즉시 전량 매도하므로, 저장 전에 action 과 무관하게 거부한다
        return {
            "ok": False,
            "error": (
                f"손절선 {decision.stop_loss} 이 현재가 {price} 이상: 모니터가 즉시 손절 매도함. "
                "현재가 아래로 다시 제출하거나 stop_loss 를 비우세요"
            ),
        }
    stage_name = resume_stage or "30_decision"
    path = write_checkpoint(d, stage_name, _wrap(decision.model_dump(), _call(agent, call_meta)))
    _ingest_run(ctx, d, date, market, ticker)
    # 수집이 알아낸 종목명·US 거래소를 원장에 남긴다 (표기·만기 스냅샷용)
    spot_ledger.upsert_ticker_name(ctx.conn, market, ticker, meta.get("name"), "inputs")
    spot_ledger.upsert_ticker_exchange(ctx.conn, market, ticker, meta.get("exchange"))
    if resume_stage:  # 재평가 결정이 최신 결정이 되도록 마지막에 적재
        spot_ledger.ingest_checkpoint(
            ctx.conn, date, market, ticker, resume_stage, read_checkpoint(d, resume_stage) or {}
        )
    m_round = _RESUME_RE.match(resume_stage) if resume_stage else None
    # 합의로 토론을 생략했는지 원장 결정에 남긴다(전후 비교용)
    summ = read_checkpoint(d, "25_research_summary") or {}
    skipped = bool((summ.get("data") or summ).get("skipped_debate"))
    if (did := spot_ledger.decision_id(ctx.conn, date, market, ticker)) is not None:
        spot_ledger.set_skipped_debate(ctx.conn, did, skipped)
    decided_at = ctx.now()  # 메모리 decided_at 은 실행 직전 시각
    executions = _execute(
        ctx, date, market, decision, inputs, price,
        resume_round=int(m_round.group(1)) if m_round else None,
        extra_note=note, change_pct=change_pct,
    )  # fmt: skip
    memory_entry_id = _record_memory(
        ctx, decision, market, date, round_no=int(m_round.group(1)) if m_round else 0,
        skipped_debate=skipped, now=decided_at,
    )  # fmt: skip
    logger.info("결정 저장 %s %s: %s (계좌 %d)", market, ticker, decision.action, len(executions))
    return {
        "ok": True,
        "path": str(path),
        "stage": stage_name,
        "action": decision.action,
        "price": str(price),
        "requote": note,
        "dry_run": ctx.dry_run,
        "executions": executions,
        "memory_entry_id": memory_entry_id,
    }


# --- status -----------------------------------------------------------------------


def status(ctx: ToolContext, *, market: str, date: date) -> dict:
    """오늘 종목별 체크포인트 목록과 세션 수."""
    if market not in ("KR", "US"):
        return {"ok": False, "error": f"market 은 KR 또는 US: {market!r}"}
    mdir = market_dir(date, market, ctx.state)
    tickers: dict[str, list[str]] = {}
    if mdir.exists():
        for p in sorted(mdir.iterdir()):
            if p.is_dir():
                tickers[p.name] = list_checkpoints(p)
    return {
        "ok": True,
        "date": date.isoformat(),
        "market": market,
        "tickers": tickers,
        "sessions_today": calls.sessions_today(ctx.conn, ctx.now()) if ctx.conn else 0,
        "cap": ctx.settings.llm.daily_session_cap,
    }
