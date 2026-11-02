"""LLM 세션이 쓰는 spot 도구 구현 (status·checkpoint list/read/write/bundle/summarize·재시세·
submit-decision).

모든 도구는 예외 대신 {"ok": bool, ...} dict 를 돌려준다(CLI 가 JSON 1줄로 그대로 출력).
순서 강제는 프롬프트가 아니라 `StageRule` 전제조건이 맡는다(`kernel.session.stages.check_rule`).
submit-decision 은 `SUBMIT_RULE` 을 본 뒤 결정을 저장하고 `executor.execute` 로 계좌별 실행을
넘긴다.
"""

from __future__ import annotations

import functools
import html
import json
import logging
import os
import re
import sqlite3
from collections.abc import Callable
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
from trader.kernel.notify import NullNotifier, make_notifier
from trader.kernel.platform import paths
from trader.kernel.registry.types import StageRule
from trader.kernel.session import calls, runner
from trader.kernel.session.run_store import checkpoint_payload as _wrap
from trader.kernel.session.run_store import (
    list_checkpoints,
    market_dir,
    read_checkpoint,
    run_dir,
    write_checkpoint,
)
from trader.kernel.session.stages import check_rule, first_rule

logger = logging.getLogger("trader.spot")

MIN_REPORTS = 2
REQUOTE_AFTER_S = 600  # 입력 수집 뒤 이만큼 지났으면 제출 때 현재가를 다시 받는다
BUNDLE_LIMIT_BYTES = 24_000  # Claude Code Bash 출력 한도(30KB, UTF-8 바이트) 아래

_ANALYST_RE = re.compile(r"^10_analyst_([a-z_]+)$")
_DEBATE_RE = re.compile(r"^20_debate_r(\d+)_(bull|bear)$")
_RESUME_RE = re.compile(r"^31_decision_r(\d+)$")
_TICKER_RE = re.compile(r"^(?!\.+$)[A-Za-z0-9.\-]{1,16}$")  # 점만 있는 값(., ..)은 거부
_STAGE_RE = re.compile(r"^\d{2}_[a-z0-9_\-]{1,40}$")

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


def _unescape(obj: Any) -> Any:
    """Claude Code 가 서브에이전트 출력의 <·>·& 를 이스케이프하므로 문자열 값을 되돌린다."""
    if isinstance(obj, str):
        return html.unescape(obj)
    if isinstance(obj, dict):
        return {k: _unescape(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_unescape(v) for v in obj]
    return obj


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
    """도구 서브프로세스의 실행 컨텍스트. policy·paused·inputs 는 accounts·inputs 포트에서 온다."""

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
        raw = acc.brokers()
        brokers = dict(raw) if isinstance(raw, dict) else {b.account.id: b for b in raw}
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
        **extra,
    )


def tool_ctx(boot: Any) -> ToolContext:
    """부트 결과(`ports=True`)로 ToolContext 를 만든다. `TRADER_DRY_RUN` 이 없거나 "1" 이면
    dry_run(주문·승인 요청 없음) — LLM 이 플래그를 빠뜨려도 주문이 나가지 않는다."""
    notifier: Any = NullNotifier()
    if boot.secrets is not None:
        try:
            notifier = make_notifier(boot.secrets)
        except (OSError, ValueError) as e:  # 알림 조립 실패는 도구를 막지 않는다(알림 없음)
            logger.warning("알림 조립 실패, 알림 없이 진행: %s", type(e).__name__)
    return ctx_from_ports(
        settings=boot.settings,
        conn=boot.conn,
        state=paths.state_dir(),
        ports=boot.ports,
        notifier=notifier,
        dry_run=os.environ.get("TRADER_DRY_RUN", "1") != "0",
    )


def _dir(ctx: ToolContext, ticker: str, market: str, d: date) -> Path:
    return run_dir(d, market, ticker, ctx.state)


def _call(agent: str | None, meta: dict | None) -> dict | None:
    if agent is None and not meta:
        return None
    base = {"agent": agent or "unknown"}
    base.update({k: v for k, v in (meta or {}).items() if k in schemas.CallMeta.model_fields})
    return schemas.CallMeta.model_validate(base).model_dump()


# --- checkpoint list ------------------------------------------------------------


@_guarded
def checkpoint_list(ctx: ToolContext, *, ticker: str, market: str, date: date) -> dict:
    return {"ok": True, "stages": list_checkpoints(_dir(ctx, ticker, market, date))}


# --- 트레이더 컨텍스트 ------------------------------------------------------------


def _mode_of(ctx: ToolContext, account: Any) -> str | None:
    if ctx.policy is not None and ctx.conn is not None:
        return ctx.policy(ctx.conn, account)[0]
    return getattr(account, "mode", None)


def _latest_positions(conn: sqlite3.Connection, account_id: str, market: str) -> dict | None:
    """계좌·시장의 최신 positions_snapshot 날짜 포지션 + 그날 NAV·현금 (없으면 None)."""
    row = conn.execute(
        "SELECT MAX(date) AS d FROM positions_snapshot WHERE account_id=? AND market=? "
        "AND asset='stock'",
        (account_id, market),
    ).fetchone()
    day = row["d"] if row else None
    nav_row = conn.execute(
        "SELECT date, nav, cash FROM account_nav WHERE account_id=? AND market=? "
        "ORDER BY date DESC, id DESC LIMIT 1",
        (account_id, market),
    ).fetchone()
    if day is None and nav_row is None:
        return None
    positions: list[dict] = []
    if day is not None:
        positions = [
            dict(r)
            for r in conn.execute(
                "SELECT ticker, market, qty, avg_price, current_price, eval_amount "
                "FROM positions_snapshot WHERE account_id=? AND market=? AND asset='stock' "
                "AND date=? AND ticker NOT LIKE '\\_CASH%' ESCAPE '\\' ORDER BY ticker",
                (account_id, market, day),
            ).fetchall()
        ]
    return {
        "date": (nav_row["date"] if nav_row else day),
        "nav": nav_row["nav"] if nav_row else None,
        "cash": nav_row["cash"] if nav_row else None,
        "positions": positions,
    }


def portfolio_context(ctx: ToolContext, market: str, d: date) -> dict | None:
    """계좌별 {env, mode, date, nav, cash, positions} (최신 스냅샷). 원장이 없으면 None.
    스냅샷은 마지막 관측값이라 `d` 로 자르지 않는다(US 운영일과 스냅샷 날짜가 어긋날 수 있다)."""
    if ctx.conn is None:
        return None
    out: dict[str, dict] = {}
    for a in ctx.accounts:
        if market not in a.markets:
            continue
        snap = _latest_positions(ctx.conn, a.id, market) or {}
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


def trader_context(ctx: ToolContext, market: str, d: date, ticker: str, inputs: dict) -> dict:
    """트레이더 입력의 파이썬 제공분: quote·risk_limits·costs_bp·portfolio·holding.
    (memory·calibration·quant·regime·theme 은 이연 — README 알려진 차이)"""
    quote = ((inputs.get("technical") or {}).get("quote") or {}).get("data")
    return {
        "quote": quote,
        "risk_limits": ctx.settings.risk.model_dump(),
        "costs_bp": costs.round_trip_bp(market),
        "portfolio": portfolio_context(ctx, market, d),
        "holding": holding_context(ctx, ticker, market, d),
    }


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


def _load_turns(d: Path) -> dict[int, dict[str, schemas.DebateTurn]]:
    """`20_debate_r<n>_<side>` 토론 턴을 {round: {side: DebateTurn}} 로."""
    turns: dict[int, dict[str, schemas.DebateTurn]] = {}
    for stage in list_checkpoints(d):
        m = _DEBATE_RE.match(stage)
        if not m:
            continue
        data = read_checkpoint(d, stage) or {}
        turns.setdefault(int(m.group(1)), {})[m.group(2)] = schemas.DebateTurn.model_validate(
            data["data"]
        )
    return turns


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

_TRADER_REPORT_KEYS = (
    "ticker",
    "asof",
    "role",
    "summary",
    "bullets",
    "signal",
    "score",
    "confidence",
    "horizon",
    "key_risks",
    "counter_evidence",
    "data_gaps",
)


def _reports(d: Path) -> list[dict]:
    out = []
    for stage in list_checkpoints(d):
        if _ANALYST_RE.match(stage):
            data = read_checkpoint(d, stage) or {}
            if isinstance(data.get("data"), dict):
                out.append(data["data"])
    return out


def _size(obj: Any) -> int:
    return len(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def _cut(v: Any, n: int) -> Any:
    if isinstance(v, str):
        return v if len(v) <= n else v[: n - 1] + "…"
    if isinstance(v, list):
        return [_cut(x, n) for x in v]
    if isinstance(v, dict):
        return {k: _cut(x, n) for k, x in v.items()}
    return v


def _trim_lists(v: Any, keep: int) -> Any:
    """`keep` 보다 긴 dict 목록은 마지막 `keep` 건만(시계열은 오름차순이라 꼬리가 최신)."""
    if isinstance(v, list):
        if len(v) > keep and all(isinstance(x, dict) for x in v):
            v = v[-keep:]
        return [_trim_lists(x, keep) for x in v]
    if isinstance(v, dict):
        return {k: _trim_lists(x, keep) for k, x in v.items()}
    return v


def _shrink(obj: dict, limit: int, *, drop_evidence: bool = False) -> dict:
    """UTF-8 바이트 한도를 넘으면 리포트의 evidence → 긴 문자열 순으로 줄인다."""
    if drop_evidence and "reports" in obj:
        obj = {
            **obj,
            "reports": [{k: r[k] for k in _TRADER_REPORT_KEYS if k in r} for r in obj["reports"]],
        }
    if _size(obj) <= limit:
        return obj
    if "reports" in obj and any("evidence" in r for r in obj["reports"]):
        return _shrink(obj, limit, drop_evidence=True)
    # 긴 목록(일봉·분봉 같은 시계열, 오름차순)은 꼬리(최신)만 남긴다 — 문자열을 자르기 전에
    for keep in (120, 60, 30):
        trimmed = _trim_lists(obj, keep)
        if _size(trimmed) <= limit:
            note = f"묶음이 {limit}바이트를 넘어 긴 목록을 최근 {keep}건으로 줄였습니다"
            return {**trimmed, "note": note}
    obj = trimmed
    for n in (300, 200, 140, 100):
        cut = {
            **_cut(obj, n),
            "note": f"묶음이 {limit}바이트를 넘어 긴 문자열을 {n}자에서 잘랐습니다",
        }
        if _size(cut) <= limit:
            return cut
    return cut


EVIDENCE_ROLES = ("technical", "fundamental", "news_macro", "sentiment")
EVIDENCE_DEFINITION = (
    "리포트가 낸 근거 항목 수(evidence)·고유 출처 수(source_id)·data_gaps·counter_evidence·"
    "key_risks 개수와 신호 방향. 파이썬이 센 개수일 뿐 검증된 근거 수가 아니다 — 중복·상충 "
    "근거도 그대로 센다"
)


def evidence_stats(reports: list[dict]) -> dict:
    """트레이더 묶음이 evidence 를 떼기 전에 원본 리포트에서 센 숫자. 프롬프트의 '검증 가능한
    근거 수' 판단은 이 숫자와 리포트 본문(bullets·summary)을 함께 본다."""
    roles: dict[str, dict] = {}
    for r in reports:
        ev = r.get("evidence") or []
        roles[str(r.get("role"))] = {
            "evidence": len(ev),
            "sources": len({e.get("source_id") for e in ev if isinstance(e, dict)}),
            "data_gaps": len(r.get("data_gaps") or []),
            "counter_evidence": len(r.get("counter_evidence") or []),
            "key_risks": len(r.get("key_risks") or []),
            "direction": summary_mod.signal_direction(r.get("signal")),
        }
    directions: dict[str, int] = {}
    for v in roles.values():
        directions[v["direction"]] = directions.get(v["direction"], 0) + 1
    top = max((n for k, n in directions.items() if k != "NEUTRAL"), default=0)
    return {
        "definition": EVIDENCE_DEFINITION,
        "roles": roles,
        "missing_roles": [x for x in EVIDENCE_ROLES if x not in roles],
        "directions": directions,
        # 방향 일치도 = 가장 많은 방향(중립 제외)의 역할 수 ÷ 받은 리포트 수
        "agreement": round(top / len(roles), 4) if roles else 0.0,
    }


def _latest_decision_stage(d: Path) -> str | None:
    """오늘의 최신 결정 단계 (31_decision_r<n> 중 n 최대, 없으면 30_decision, 둘 다 없으면 None)."""
    stages = list_checkpoints(d)
    rounds = [int(m.group(1)) for s in stages if (m := _RESUME_RE.match(s))]
    if rounds:
        return f"31_decision_r{max(rounds)}"
    return "30_decision" if "30_decision" in stages else None


def _brief_turn(t: dict) -> dict:
    """우선순위가 아닌 쪽 라운드의 턴은 핵심만 남긴다 (트레이더 묶음 크기 절감)."""
    return {
        k: t.get(k) for k in ("side", "round", "stance", "valuation_view", "timing", "conviction")
    }


def _trader_summary(summ: dict) -> dict:
    """preferred 쪽 턴은 전문, 반대쪽은 핵심만 (트레이더 md 가 preferred 를 우선한다)."""
    if summ.get("skipped_debate"):
        return dict(summ)  # 토론 생략(합의) 요약은 턴이 없어 줄일 것이 없다
    out = dict(summ)
    other = "final" if summ.get("preferred") == "round0" else "round0"
    if isinstance(summ.get(other), dict):
        out[other] = {
            side: _brief_turn(t) for side, t in summ[other].items() if isinstance(t, dict)
        }
    # issues 는 final 양측 rebuttals 의 복제라 트레이더 묶음에선 뺀다
    out.pop("issues", None)
    out["note"] = f"{other} 턴은 핵심만, issues 는 final.rebuttals 와 같아 생략"
    return out


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
    계좌마다. 재평가 라운드는 41_/51_ 체크포인트 이름에 들어간다. (메모리 기록은 M8)"""
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
    executions = _execute(
        ctx, day, market, decision, inputs, price,
        skip_accounts=frozenset(executed), extra_note=note, change_pct=change_pct,
    )  # fmt: skip
    logger.warning("미실행 결정의 실행 재개 %s %s: %s", market, ticker, decision.action)
    return out | {
        "resumed_execution": True,
        "message": "30_decision 은 있었지만 판정이 없어 실행을 재개함",
        "executions": executions,
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
    (30_decision 이 있어야 함; 트리거 가격은 M5 — 지금은 입력가 + 재시세).

    같은 날 `30_decision` 이 이미 있으면 기존 결정을 돌려주고(`already`), 실행이 끝나지 않은 계좌만
    재개한다. 원장 연결이 없으면 `30_decision` 을 쓰기 전에 거부한다(재시도가 멱등 분기에 걸리지
    않게). 반환 {ok, path, stage, action, price, requote, dry_run, executions, resumed_execution?,
    already?}."""
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
    price, note, change_pct = requote(ctx, market, ticker, meta.get("exchange"), inputs, ctx.now())
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
    executions = _execute(
        ctx, date, market, decision, inputs, price,
        resume_round=int(m_round.group(1)) if m_round else None,
        extra_note=note, change_pct=change_pct,
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
