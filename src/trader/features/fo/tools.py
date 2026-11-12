"""파생 세션 도구(Phase 26): `trader tool fo bundle|write|summarize`·
`trader tool submit-fo-decision`.

시장 디렉터리(`market_dir(date, "KR")`)의 `_fo_*` 단계. 쓰기 허용은 애널리스트 3역할·토론 턴뿐이고
`_fo_summary` 는 summarize, `_fo_decision*` 는 submit 전용. 재평가는 파이썬이 세션 앞에서 남긴
`_fo_reeval_r<n>_<계좌>` 요청(계좌·포지션·버전·request_id)과 봉투가 같아야 받는다.

M10(A7): 옛 `orchestrator/tools.py` 의 fo 구간을 옮겼다. 도구 컨텍스트는 `FoToolContext`(옛 공용
`ToolContext` 의 fo 관련 필드만)이고 `tool_ctx(boot)` 가 포트로 조립한다(fo 계좌·브로커는
`ports["accounts"].fo_accounts()/fo_brokers()`, 마스터는 오프라인 캐시). 토론 턴 읽기는 커널
`tooling.load_turns`, 묶음 줄이기는 `tooling.shrink`, 트레이더 요약은 `tooling.trader_summary`,
검증·요약은 `tooling.validate_payload`·`summarize_turns`(regime 과 공용)를 쓴다. 주문은
`executor.run_leg` 로만.
출력은 stdout JSON 1줄(커널 `run_tool`), dry_run 은 env `TRADER_DRY_RUN` 으로만 정한다.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import typer
from pydantic import ValidationError

from trader.features.fo import executor as fo_exec
from trader.features.fo import inputs as fo_inputs
from trader.features.fo import rules as fo_rules
from trader.features.fo import sizing as fo_sizing
from trader.features.fo import store as fo_store
from trader.features.fo.research import RESEARCH
from trader.features.fo.schemas import FoDecisionEnvelope
from trader.features.fo.types import FoExecContext
from trader.kernel.broker import fo_master
from trader.kernel.cli.tool_cmd import emit, run_tool
from trader.kernel.config.market_hours import is_fo_open
from trader.kernel.execution.types import OrderBookkeepingError
from trader.kernel.ledger import control
from trader.kernel.notify.boot import boot_notifier
from trader.kernel.notify.telegram import Notifier
from trader.kernel.platform import paths
from trader.kernel.registry.types import StageRule
from trader.kernel.session import research_policy, scope, tooling
from trader.kernel.session.run_store import checkpoint_payload as _wrap
from trader.kernel.session.run_store import (
    list_checkpoints,
    market_dir,
    read_checkpoint,
    write_checkpoint,
)
from trader.kernel.session.schemas import AnalystReport, DebateTurn

logger = logging.getLogger("trader.fo.tools")

BUNDLE_LIMIT_BYTES = 24_000  # Claude Code Bash 출력 한도(30KB, UTF-8 바이트) 아래

FO_MARKET = "KR"
FO_INPUTS = "_fo_inputs"
FO_SUMMARY = "_fo_summary"
FO_MIN_ROLES = 2
_FO_ANALYST_RE = re.compile(r"^_fo_analyst_(technical|sentiment|news_macro)$")
_FO_DEBATE_RE = tooling.debate_re(RESEARCH.turn_prefix, RESEARCH.cross_turn_prefix)
_FO_DECISION_RE = re.compile(r"^_fo_decision_r(\d+)_([A-Za-z0-9_-]+)$")
_FO_REEVAL_RE = re.compile(r"^_fo_reeval_r(\d+)_([A-Za-z0-9_-]+)$")
_FO_WRITABLE = (
    "_fo_analyst_<technical|sentiment|news_macro>",
    "_fo_debate_r<n>_<bull|bear>",
    "_fo_debatex_r<n>_<bull|bear>",
)
FO_PENDING_FLAG = "fo_pending_entry:"
FO_STALE_NOTE = "입력 노후 — 실행 안 함"
FO_INPUT_DATE_NOTE = "입력 날짜 불일치 — 실행 안 함"
FO_SUBMIT_STALE_NOTE = "제출·재개 시점 입력 노후 — 실행 안 함"

# 전제조건 표(`StageRule`) — `fo write` 는 입력이 있어야 하고, 제출은 서로 다른 애널리스트 역할 ≥ 2
# (단계 이름 = 역할이라 개수 = 역할 수) + 판 a r0 양측 + 요약. 핸들러는 옛 문구 그대로 같은 조건을
# 본다. 교차 토론(판 b `_fo_debatex_*`)의 완료는 `research_policy.completion_error` 가 강제한다
FO_WRITE_RULES = (
    StageRule("_fo_analyst_*", requires=(FO_INPUTS,)),
    StageRule("_fo_debate_r*_*", requires=(FO_INPUTS,)),
    StageRule("_fo_debatex_r*_*", requires=(FO_INPUTS,)),
)
FO_SUBMIT_RULE = StageRule(
    "_fo_decision*",
    requires=(FO_INPUTS, "_fo_debate_r0_bull", "_fo_debate_r0_bear", FO_SUMMARY),
    min_counts=(("_fo_analyst_*", FO_MIN_ROLES),),
)


@dataclass
class FoToolContext:
    """파생 도구의 실행 컨텍스트. `fo` 는 실행기 컨텍스트(파생 계좌가 없으면 None — 제출 거부)."""

    settings: Any
    conn: sqlite3.Connection | None
    state: Path | None = None
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    dry_run: bool = True
    fo: FoExecContext | None = None


def _fo_exec_ctx(boot: Any, *, notifier: Notifier, dry_run: bool) -> FoExecContext | None:
    """파생 세션 도구의 실행 컨텍스트(옛 `cli._fo_tool_ctx`). `asset: fo` 계좌가 없으면 None —
    `tool fo *`·`submit-fo-decision` 은 그때 거부한다. 마스터는 캐시만(오프라인), 국면·시장 뷰는
    원장(`executor.context_sources`). 과거 asof 는 `_fo_inputs.meta.historical` 하나가 출처."""
    acc = boot.ports.get("accounts")
    fo_accounts = list(acc.fo_accounts()) if acc is not None else []
    if not fo_accounts:
        return None
    brokers = dict(acc.fo_brokers())
    try:
        master = fo_master.load(cache_dir=paths.state_dir() / "fo", offline=True)
    except Exception as e:  # noqa: BLE001 — 마스터 없이도 보유분 처리는 가능(신규는 게이트가 거부)
        logging.getLogger("trader.fo").warning("파생 마스터 적재 실패(빈 목록으로 진행): %s", e)
        master = []
    try:
        market_client = acc.market_client()
    except Exception:  # noqa: BLE001 - 시세 없이도 기록·보유 처리는 간다
        market_client = None

    def now() -> datetime:
        return datetime.now(UTC)

    return FoExecContext(
        settings=boot.settings,
        accounts=fo_accounts,
        conn=boot.conn,
        brokers=brokers,
        market_client=market_client,
        master=master,
        notifier=notifier,
        state=paths.state_dir(),
        dry_run=dry_run,
        secrets=boot.secrets,
        ports=boot.ports,
        **fo_exec.context_sources(boot.conn, now, boot.ports),
    )


def tool_ctx(boot: Any) -> FoToolContext:
    """부트 결과(`ports=True`)로 FoToolContext 를 만든다. `TRADER_DRY_RUN` 이 없거나 "1" 이면
    dry_run(주문 없음) — LLM 이 플래그를 빠뜨려도 주문이 나가지 않는다."""
    dry_run = scope.dry_run()
    notifier = boot_notifier(boot)
    return FoToolContext(
        settings=boot.settings,
        conn=boot.conn,
        state=paths.state_dir(),
        dry_run=dry_run,
        fo=_fo_exec_ctx(boot, notifier=notifier, dry_run=dry_run),
    )


def _fo_dir(ctx: FoToolContext, d: date, *, create: bool = False) -> Path:
    return market_dir(d, FO_MARKET, ctx.state, create=create)


def _fo_inputs(ctx: FoToolContext, d: date) -> dict | None:
    cp = read_checkpoint(_fo_dir(ctx, d), FO_INPUTS)
    data = cp.get("data") if isinstance(cp, dict) else None
    return data if isinstance(data, dict) else None


def _fo_reports(d: Path) -> list[dict]:
    out = []
    for stage in list_checkpoints(d):
        if _FO_ANALYST_RE.match(stage):
            data = read_checkpoint(d, stage) or {}
            if isinstance(data.get("data"), dict):
                out.append(data["data"])
    return out


def _fo_live_request(d: Path, account_id: str, position_id: int) -> tuple[int, dict] | None:
    """아직 결정이 들어오지 않은 이 포지션의 재평가 요청(라운드, 봉투) — 없으면 None."""
    decided = {r for r, _ in _fo_decision_stages(d, account_id)}
    for r, req in reversed(_fo_reeval_requests(d, account_id)):
        if req.get("position_id") == position_id and r not in decided:
            return r, req
    return None


def fo_pending_entry(conn: sqlite3.Connection, account_id: str, today: date) -> dict | None:
    """진입 창 밖에서 보류된 아침 결정(`control.fo_pending_entry:<계좌>`)의 소비자. 오늘 운영일
    것만 돌려주고 다른 날짜·dry_run 기록은 지운다(어제 보류분이 오늘 실행되지 않게, S3 P0)."""
    key = f"{FO_PENDING_FLAG}{account_id}"
    flag = control.get_flag(conn, key)
    if not isinstance(flag, dict):
        return None
    if flag.get("date") != today.isoformat() or flag.get("dry_run"):
        control.set_flag(conn, key, None)
        return None
    return flag


def _fo_decision_stages(d: Path, account_id: str | None = None) -> list[tuple[int, str]]:
    """오늘의 결정 단계 [(round, stage)] — 아침(0) + 재평가(계좌 지정 시 그 계좌만)."""
    out: list[tuple[int, str]] = []
    for stage in list_checkpoints(d):
        if stage == "_fo_decision":
            out.append((0, stage))
            continue
        m = _FO_DECISION_RE.match(stage)
        if m and (account_id is None or m.group(2) == account_id):
            out.append((int(m.group(1)), stage))
    return sorted(out)


def _fo_reeval_requests(d: Path, account_id: str) -> list[tuple[int, dict]]:
    out: list[tuple[int, dict]] = []
    for stage in list_checkpoints(d):
        m = _FO_REEVAL_RE.match(stage)
        if m and m.group(2) == account_id:
            data = (read_checkpoint(d, stage) or {}).get("data") or {}
            out.append((int(m.group(1)), data))
    return sorted(out, key=lambda x: x[0])


def _fo_position_view(ctx: FoToolContext, pos: Any) -> dict:
    """fo-trader 입력의 `context.position`(재평가): 현재 손절·배수·수명 예산 잔여."""
    cfg = ctx.settings.fo
    snap = ctx.conn.execute(
        "SELECT avg FROM fo_positions_snapshot WHERE account_id = ? AND code = ? "
        "ORDER BY date DESC LIMIT 1",
        (pos.account_id, pos.code),
    ).fetchone() if ctx.conn is not None else None  # fmt: skip
    avg = Decimal(snap["avg"]) if snap and snap["avg"] else None
    budget = fo_sizing.position_budget(
        pos, ref_price=avg, multiplier=cfg.product_cfg().multiplier, r=cfg.risk_per_trade_pct
    )
    out = {
        k: getattr(pos, k)
        for k in (
            "id", "version", "account_id", "code", "kind", "contract_side", "direction",
            "vehicle", "status", "qty_held", "qty_target", "stop_pt", "stop_fut", "tp_pt",
            "leverage_used", "approved_mult", "premium_stop", "entry_premium", "horizon_until",
            "last_trade_date", "decision_date", "round", "opened_at",
        )
    }  # fmt: skip
    # remaining = 한도 − 실현·비용 − 보유분 계획 손실(추가로 쓸 수 있는 위험). 계획 손실을 모르면
    # None(확정값으로 주지 않는다, 단계 5 리뷰)
    out["budget"] = {
        "used": budget["used"], "planned": budget["planned"], "limit": budget["limit"],
        "remaining": budget["free"],
    }  # fmt: skip
    return json.loads(json.dumps(out, default=str, ensure_ascii=False))


def _fo_require_fo(ctx: FoToolContext) -> dict | None:
    if ctx.fo is None:
        return {"ok": False, "error": "파생 실행 컨텍스트 없음(asset: fo 계좌가 없거나 도구 밖)"}
    return None


def fo_bundle(
    ctx: FoToolContext,
    *,
    date: date,
    kind: str,
    role: str | None = None,
    round: int | None = None,
    side: str | None = None,
    position_id: int | None = None,
    account: str | None = None,
    lane: str = "a",
) -> dict:
    """파생 세션 서브에이전트 입력 묶음. kind=analyst(role) | researcher(round, side, lane — 직전
    턴은 같은 판에서만 읽는다) |
    trader(재평가면 position_id·account → `_fo_reeval_r<n>_<계좌>` 요청의 트리거·request_id;
    `round` 를 주면 그 라운드의 요청만 읽고 없으면 오류 — stdin `position.round` 와 묶음이 어긋나지
    않게(S3 P1))."""
    d = _fo_dir(ctx, date)
    inputs = _fo_inputs(ctx, date)
    if inputs is None:
        return {"ok": False, "error": f"{FO_INPUTS} 없음 (파이썬이 세션 앞에서 만든다)"}
    if kind == "analyst":
        if role not in ("technical", "sentiment", "news_macro"):
            return {"ok": False, "error": "analyst 묶음은 --role technical|sentiment|news_macro"}
        return {
            "ok": True,
            "data": tooling.shrink(
                {
                    **fo_inputs.bundle(inputs, kind="analyst", role=role),
                    "input_context": tooling.compact_context(inputs),
                },
                BUNDLE_LIMIT_BYTES,
            ),
        }
    reports = _fo_reports(d)
    if not reports:
        return {"ok": False, "error": "_fo_analyst_* 없음 (애널리스트 먼저)"}
    if kind == "researcher":
        if round is None or side not in ("bull", "bear"):
            return {
                "ok": False,
                "error": "researcher 묶음은 --round 와 --side(bull|bear) 가 필요합니다",
            }
        if err := research_policy.lane_error(ctx.conn, ctx.settings, d, RESEARCH, lane):
            return {"ok": False, "error": err}
        try:
            own, opp = tooling.previous_turns(
                d, _FO_DEBATE_RE, lane=lane, round_no=round, side=side
            )
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        data = fo_inputs.bundle(
            inputs, kind="researcher", round=round, side=side, reports=reports,
            previous=(own.model_dump() if own else None, opp.model_dump() if opp else None),
        )  # fmt: skip
        data["input_context"] = tooling.compact_context(inputs)
        data["lane"] = lane
        data["research_policy"] = research_policy.public_policy(ctx.conn, ctx.settings, d, RESEARCH)
        return tooling.debate_bundle(data, BUNDLE_LIMIT_BYTES)
    if kind == "trader":
        if not _fo_decision_stages(d):
            if err := research_policy.completion_error(ctx.conn, ctx.settings, d, RESEARCH):
                return {"ok": False, "error": err}
        summ = read_checkpoint(d, FO_SUMMARY)
        if summ is None:
            return {"ok": False, "error": f"{FO_SUMMARY} 없음 (fo summarize 먼저)"}
        position = trigger = None
        request_id = (inputs.get("meta") or {}).get("request_id")
        next_round = 0
        if position_id is not None or account is not None:
            if position_id is None or account is None or ctx.conn is None:
                return {"ok": False, "error": "재평가 묶음은 --position-id 와 --account 둘 다"}
            pos = fo_store.get_position(ctx.conn, position_id)
            if pos is None or pos.account_id != account:
                return {"ok": False, "error": f"포지션 {position_id} 이 계좌 {account} 에 없음"}
            position = _fo_position_view(ctx, pos)
            reqs = [r for r in _fo_reeval_requests(d, account) if r[1].get("position_id") == pos.id]
            if round is not None:
                reqs = [r for r in reqs if r[0] == round]
                if not reqs:
                    return {"ok": False, "error": f"재평가 요청 _fo_reeval_r{round}_{account} 없음"}
            if reqs:
                next_round, req = reqs[-1]
                trigger = req.get("trigger")
                request_id = req.get("request_id") or request_id
            else:
                next_round = 1 + max((r for r, _ in _fo_decision_stages(d, account)), default=0)
        decs = _fo_decision_stages(d, account)
        prev_stage = decs[-1][1] if decs else None
        prev = (read_checkpoint(d, prev_stage) or {}).get("data") if prev_stage else None
        data = fo_inputs.bundle(
            inputs, kind="trader", reports=reports,
            summary=tooling.trader_summary(summ.get("data", summ)), position=position,
            trigger=trigger,
        )  # fmt: skip
        data["input_context"] = tooling.compact_context(inputs)
        data["request_id"] = request_id
        data["previous_decision"] = prev
        data["previous_decision_stage"] = prev_stage
        data["next_round"] = next_round
        return {"ok": True, "data": tooling.shrink(data, BUNDLE_LIMIT_BYTES, drop_evidence=True)}
    return {"ok": False, "error": f"알 수 없는 kind: {kind} (analyst|researcher|trader)"}


def fo_write(
    ctx: FoToolContext,
    *,
    date: date,
    stage: str,
    payload: dict,
    agent: str | None = None,
    call_meta: dict | None = None,
) -> dict:
    """LLM 쓰기 가능 파생 단계: `_fo_analyst_<role>`(AnalystReport, role 일치),
    `_fo_debate_r<n>_<side>`·판 b `_fo_debatex_r<n>_<side>`(DebateTurn, round·side 일치,
    n ≤ debate_rounds). 토론 턴을 새로 쓰면
    `_fo_summary` 를 지운다(요약은 턴의 함수)."""
    d = _fo_dir(ctx, date)
    md = tooling.parse_debate_stage(_FO_DEBATE_RE, stage)
    lane = "a"
    if md:
        lane = md[0]
        if err := research_policy.write_error(
            ctx.conn, ctx.settings, d, RESEARCH, round_no=md[1], side=md[2], lane=lane
        ):
            return {"ok": False, "error": err}
        call_meta = {**(call_meta or {}), "provider": "claude"}
    rounds = research_policy.rounds(ctx.conn, ctx.settings, d, RESEARCH)
    ma = _FO_ANALYST_RE.match(stage)
    md = tooling.parse_debate_stage(_FO_DEBATE_RE, stage)
    if ma:
        model: type = AnalystReport
    elif md:
        model = DebateTurn
        if md[1] > rounds:
            return {
                "ok": False,
                "error": (
                    f"라운드 {md[1]} 는 설정(debate_rounds="
                    f"{ctx.settings.debate_rounds})을 넘습니다"
                ),
            }
    else:
        return {
            "ok": False,
            "error": f"쓸 수 없는 단계: {stage} (허용: {', '.join(_FO_WRITABLE)})",
        }
    v, err = tooling.validate_payload(model, payload)
    if err:
        return err
    if ma and v.role != ma.group(1):
        return {
            "ok": False,
            "error": f"파일명 role({ma.group(1)}) 과 페이로드 role({v.role}) 불일치",
        }
    inputs = _fo_inputs(ctx, date)
    if inputs is None:
        return {"ok": False, "error": f"{FO_INPUTS} 없음 (파생 세션 밖에서는 쓸 수 없다)"}
    proxy = (inputs.get("meta") or {}).get("proxy")
    if ma and proxy and v.ticker != proxy:
        return {
            "ok": False,
            "error": f"리포트 ticker 는 입력의 proxy({proxy})여야 합니다: {v.ticker!r}",
        }
    if md and (v.round != md[1] or v.side != md[2]):
        return {
            "ok": False,
            "error": (
                f"파일명 r{md[1]}_{md[2]} 와 페이로드 "
                f"round={v.round} side={v.side} 불일치"
            ),
        }
    d = _fo_dir(ctx, date, create=True)
    if isinstance(v, DebateTurn):
        if err := research_policy.turn_error(
            ctx.conn, ctx.settings, d, RESEARCH, v.model_dump(), lane=lane
        ):
            return {"ok": False, "error": err}
    invalidated = research_policy.invalidate_summary(d, RESEARCH) if md else False
    path = write_checkpoint(d, stage, _wrap(v.model_dump(), tooling.call_meta(agent, call_meta)))
    return {"ok": True, "path": str(path), "stage": stage, "summary_invalidated": invalidated}


def fo_summarize(ctx: FoToolContext, *, date: date) -> dict:
    """`_fo_debate_*`(판 b 는 `_fo_debatex_*`) 를 모아 `_fo_summary`(수렴 감시 포함)를 만든다.
    두 판이면 같은 편을 합친 요약에 판별 원본 `by_lane` 이 붙는다."""
    d = _fo_dir(ctx, date)
    if err := research_policy.completion_error(ctx.conn, ctx.settings, d, RESEARCH):
        return {"ok": False, "error": err}
    if err := research_policy.summary_lane_error(ctx.conn, ctx.settings, d, RESEARCH):
        return {"ok": False, "error": err}
    return tooling.summarize_turns(d, FO_SUMMARY, _FO_DEBATE_RE)


def fo_reeval_request(
    ctx: FoToolContext,
    *,
    date: date,
    account_id: str,
    position_id: int,
    trigger: dict | None,
) -> dict:
    """재평가 요청(파이썬·단계 4 트리거가 세션 앞에서 부른다): `_fo_reeval_r<n>_<계좌>` 에 봉투
    {account_id, position_id, expected_version, request_id, round, trigger} 를 남기고 돌려준다.
    세션 stdin `position` 이 이것이고, `submit-fo-decision --stage _fo_decision_r<n>_<계좌>` 는
    봉투가 이 요청과 같을 때만 받는다. 같은 포지션·같은 버전의 **미결 요청이 있으면 새 라운드를
    만들지 않고 그것을 돌려준다**(`reused: true`, 트리거는 먼저 든 것) — 두 트리거가 라운드
    번호를 다투지 않게. 버전이 바뀐 미결 요청은 어차피 제출이 거부되므로 새 라운드다(S3 P1)."""
    import uuid

    if ctx.conn is None:
        return {"ok": False, "error": "원장 연결 없음"}
    pos = fo_store.get_position(ctx.conn, position_id)
    if pos is None or pos.account_id != account_id:
        return {"ok": False, "error": f"포지션 {position_id} 이 계좌 {account_id} 에 없음"}
    d = _fo_dir(ctx, date, create=True)
    live = _fo_live_request(d, account_id, pos.id)
    if live is not None and live[1].get("expected_version") == pos.version:
        n, req = live
        return {
            "ok": True, "path": str(d / f"_fo_reeval_r{n}_{account_id}.json"),
            "stage": f"_fo_reeval_r{n}_{account_id}", "round": n, "position": req, "reused": True,
        }  # fmt: skip
    used = [r for r, _ in _fo_decision_stages(d, account_id)] + [
        r for r, _ in _fo_reeval_requests(d, account_id)
    ]
    n = max(used, default=0) + 1
    stage = f"_fo_reeval_r{n}_{account_id}"
    position = {
        "account_id": account_id,
        "position_id": pos.id,
        "expected_version": pos.version,
        "request_id": uuid.uuid4().hex,
        "round": n,
        "trigger": trigger,
        "requested_at": ctx.now().isoformat(),
    }
    path = write_checkpoint(d, stage, _wrap(position, None))
    return {"ok": True, "path": str(path), "stage": stage, "round": n, "position": position}


def _fo_resolved(ctx: FoToolContext, inputs: dict, env: Any, pos: Any, *, today: date) -> dict:
    """결정 저장 때 붙이는 계약 해석 {code, kind, strike, days_left, multiplier, index_at_decision,
    quote_asof, expiry_ym}. 실행기와 같은 규칙(`executor.resolve_contract`)."""
    fo = ctx.fo
    cctx = (inputs.get("context") or {}).get("data") or {}
    idx = (cctx.get("index") or {}).get("price")
    underlying = Decimal(str(idx)) if idx not in (None, "") else None
    d = env.decision
    contract = None
    if pos is not None or d.direction != "FLAT":
        try:
            contract = fo_exec.resolve_contract(fo, env, pos, underlying=underlying, today=today)
        except Exception as e:  # noqa: BLE001 — 해석 실패는 기록(실행기가 다시 시도·거부)
            logger.warning("파생 계약 해석 실패: %s", e)
    code = pos.code if pos is not None else (contract.code if contract else None)
    kind = pos.kind if pos is not None else (contract.kind if contract else None)
    quote: dict | None = None
    tech = ((inputs.get("technical") or {}).get("data") or {}).get("quote") or {}
    if code is not None:
        if isinstance(tech.get("data"), dict) and tech["data"].get("code") == code:
            quote = tech["data"]
        else:
            quote = next((q for q in cctx.get("option_chain") or [] if q.get("code") == code), None)
    days_left = quote.get("days_left") if quote else None
    if days_left is None:
        days_left = fo_exec.days_left_of(None, contract, today)
    return {
        "code": code,
        "kind": kind,
        "strike": str(contract.strike) if contract and contract.strike is not None else None,
        "expiry_ym": contract.expiry_ym if contract else None,
        "days_left": days_left,
        "multiplier": ctx.settings.fo.product_cfg().multiplier,
        "index_at_decision": str(underlying) if underlying is not None else None,
        "index_asof": (cctx.get("index") or {}).get("asof"),
        "quote_asof": quote.get("quote_asof") if quote else None,
        "quote_price": quote.get("price") if quote else None,
    }


def submit_fo_decision(
    ctx: FoToolContext,
    *,
    date: date,
    payload: dict,
    stage: str | None = None,
    agent: str = "fo-trader",
    call_meta: dict | None = None,
) -> dict:
    """파생 결정 제출. 전제: 서로 다른 애널리스트 역할 ≥ 2 + r0 양측 턴 + `_fo_summary`.
    아침(`_fo_decision`)은 봉투의 계좌·포지션이 비어 있고 request_id 가 `_fo_inputs` 의 것과 같아야
    하며, 계좌마다 실행 창 안이면 `executor.run_leg`(실행 직전 전체 재계산), 밖이면
    `control.fo_pending_entry:<계좌>` 에 보류한다(과거 asof 입력은 기록만). 재평가
    (`_fo_decision_r<n>_<계좌>`)는 `_fo_reeval_r<n>_<계좌>` 요청과 봉투가 같고 포지션 버전이
    그대로일 때만 받아 저장·`fo_decisions` 행 + `ctx.fo.reeval_apply` 훅. 같은 단계 재제출은
    `already: true`."""
    err = _fo_require_fo(ctx)
    if err:
        return err
    fo = ctx.fo
    assert fo is not None
    if ctx.conn is None:
        return {"ok": False, "error": "원장 연결이 없어 결정을 기록할 수 없음"}
    inputs = _fo_inputs(ctx, date)
    if inputs is None:
        return {"ok": False, "error": f"{FO_INPUTS} 없음 (파이썬이 세션 앞에서 만든다)"}
    d = _fo_dir(ctx, date, create=True)
    stages = list_checkpoints(d)
    round_n, acct_id = 0, None
    if stage is not None and stage != "_fo_decision":
        m = _FO_DECISION_RE.match(stage)
        if not m or int(m.group(1)) < 1:
            return {
                "ok": False,
                "error": f"stage 는 _fo_decision 또는 _fo_decision_r<n>_<계좌>: {stage!r}",
            }
        round_n, acct_id = int(m.group(1)), m.group(2)
    stage = stage or "_fo_decision"
    if stage in stages:
        existing = (read_checkpoint(d, stage) or {}).get("data") or {}
        dec = existing.get("decision") or {}
        out = {
            "ok": True, "already": True, "stage": stage, "path": str(d / f"{stage}.json"),
            "intent": dec.get("intent"), "direction": dec.get("direction"),
            "vehicle": dec.get("vehicle"), "message": f"{stage} 이 이미 있어 기존 결정을 유지함",
        }  # fmt: skip
        if round_n == 0 and existing.get("decision"):
            # 저장 뒤 계좌 루프가 끊긴 경우: 판정(`_fo_risk_check_<계좌>`)도 보류 플래그도 없는
            # 계좌만 재개한다(접수된 계좌는 판정 파일이 있어 다시 내지 않는다, S3 P0)
            try:
                env_prev = FoDecisionEnvelope.model_validate(
                    {k: existing.get(k) for k in FoDecisionEnvelope.model_fields}
                )
            except ValidationError:
                return out
            todo = [
                a for a in fo.accounts
                if a.asset == "fo"
                and not _fo_account_done(ctx, d, stages, a.id, date, env_prev.request_id)
            ]  # fmt: skip
            if todo:
                res = _fo_run_accounts(
                    ctx, date=date, env=env_prev, stage=stage, inputs=inputs, accounts=todo,
                    record_only=existing.get("record_only") or _fo_submit_stale(ctx, inputs),
                )  # fmt: skip
                return out | res | {"resumed_execution": True}
        return out
    if round_n == 0:
        if err := research_policy.completion_error(ctx.conn, ctx.settings, d, RESEARCH):
            return {"ok": False, "error": err}
    roles = {m.group(1) for s in stages if (m := _FO_ANALYST_RE.match(s))}
    missing: list[str] = []
    if len(roles) < FO_MIN_ROLES:
        missing.append(f"서로 다른 애널리스트 역할 {len(roles)}개 (최소 {FO_MIN_ROLES})")
    if not {"_fo_debate_r0_bull", "_fo_debate_r0_bear"} <= set(stages):
        missing.append("r0 양측 토론 턴 없음")
    if FO_SUMMARY not in stages:
        missing.append(f"{FO_SUMMARY} 없음")
    if missing:
        return {"ok": False, "error": "전제조건 미충족: " + ", ".join(missing)}
    env, err = tooling.validate_payload(FoDecisionEnvelope, payload)
    if err:
        return err
    meta = inputs.get("meta") or {}
    pos = None
    if round_n == 0:
        for k in ("account_id", "position_id", "expected_version"):
            if getattr(env, k) is not None:
                return {
                    "ok": False,
                    "error": f"아침 결정 봉투는 {k} 를 비워야 합니다(계좌별 판정은 파이썬)",
                }
        if env.request_id != meta.get("request_id"):
            return {
                "ok": False,
                "error": (
                    f"request_id 불일치: 입력 {meta.get('request_id')} ≠ 봉투 {env.request_id}"
                ),
            }
    else:
        req = (read_checkpoint(d, f"_fo_reeval_r{round_n}_{acct_id}") or {}).get("data") or {}
        if not req:
            return {"ok": False, "error": f"재평가 요청 _fo_reeval_r{round_n}_{acct_id} 없음"}
        for k in ("account_id", "position_id", "expected_version", "request_id"):
            if getattr(env, k) != req.get(k):
                return {
                    "ok": False,
                    "error": (
                        f"봉투 {k}={getattr(env, k)!r} 이 요청의 버전·값 {req.get(k)!r} 과 다름"
                    ),
                }
        pos = fo_store.get_position(ctx.conn, env.position_id)  # type: ignore[arg-type]
        if pos is None or pos.account_id != acct_id:
            return {"ok": False, "error": f"포지션 {env.position_id} 이 계좌 {acct_id} 에 없음"}
        if pos.version != env.expected_version:
            return {
                "ok": False,
                "error": (
                    f"포지션 버전 불일치 {pos.version} ≠ expected_version {env.expected_version}"
                ),
            }
    resolved = _fo_resolved(ctx, inputs, env, pos, today=date)
    if round_n > 0 and getattr(fo, "market_client", None) is not None:
        # 재평가의 기준 지수는 결정 시점의 지수(아침 `_fo_inputs` 의 값이 아님 — move 트리거 기준)
        try:
            idx, _ = fo.market_client.get_kospi200()
            if idx is not None:
                resolved["index_at_decision"] = str(idx)
        except Exception as e:  # noqa: BLE001 — 실패면 아침 입력 값 유지
            logger.warning("재평가 기준 지수 조회 실패(아침 값 유지): %s", e)
    historical = bool(meta.get("historical")) or bool(getattr(fo, "historical", False))
    # 기록만 하는 사유(실행 없음): 과거 asof 입력, 다른 운영일의 입력(S3 P0)
    record_only: str | None = None
    if historical:
        record_only = "historical"
    elif meta.get("date") and meta.get("date") != date.isoformat():
        record_only = "input_date"
    elif round_n == 0:
        record_only = _fo_submit_stale(ctx, inputs)
    data = {
        **env.model_dump(), "resolved": resolved, "stage": stage, "round": round_n,
        "historical": historical, "asof": meta.get("asof"), "record_only": record_only,
    }  # fmt: skip
    call = tooling.call_meta(agent, call_meta)
    path = write_checkpoint(d, stage, _wrap(data, call))
    dec = env.decision
    try:
        fo_store.record_decision(
            ctx.conn, date=date.isoformat(), account_id=env.account_id,
            position_id=env.position_id, round=round_n, request_id=env.request_id,
            intent=dec.intent, direction=dec.direction, vehicle=dec.vehicle,
            target_leverage=dec.target_leverage, confidence=dec.confidence,
            index_at_decision=resolved.get("index_at_decision"),
            quote_asof=resolved.get("quote_asof"),
            data=json.dumps(data, ensure_ascii=False, default=str),
        )  # fmt: skip
        ctx.conn.commit()
    except sqlite3.IntegrityError as e:
        ctx.conn.rollback()
        logger.warning("fo_decisions 중복(이미 기록됨) %s r%s: %s", stage, round_n, e)
    out: dict[str, Any] = {
        "ok": True, "path": str(path), "stage": stage, "round": round_n, "intent": dec.intent,
        "direction": dec.direction, "vehicle": dec.vehicle, "resolved": resolved,
    }  # fmt: skip
    if round_n > 0:
        # 재평가 적용은 `fo/reeval.apply`(늦은 재평가 CAS → run_leg). `reeval_apply` 훅은 테스트용
        if fo.reeval_apply is not None:
            applied = fo.reeval_apply(env, round_n)
        else:
            from trader.features.fo import reeval as fo_reeval  # Task 7 모듈(지연 import)

            applied = fo_reeval.apply(fo, env, round_n=round_n)
        executed = isinstance(applied, dict) and "discarded" not in applied
        return out | {"applied": applied, "executed": executed}
    return out | _fo_run_accounts(
        ctx, date=date, env=env, stage=stage, inputs=inputs,
        accounts=[a for a in fo.accounts if a.asset == "fo"], record_only=record_only,
    )  # fmt: skip


def _fo_submit_stale(ctx: FoToolContext, inputs: dict) -> str | None:
    """제출·재개 시점 신선도(PR #61 리뷰): `_fo_inputs.meta.asof` 가 세션 한 번의 길이
    (`llm.timeout_s`) + `fo.max_input_age_s` 보다 오래됐으면 `"stale_submit"`. 진입 창 밖 보류는
    `run_leg` 가 실행 직전 다시 받아 재계산하므로 이 검사는 늦은 제출·몇 시간 뒤 재개만 막는다."""
    raw = (inputs.get("meta") or {}).get("asof")
    if not raw:
        return None
    try:
        at = datetime.fromisoformat(str(raw))
    except ValueError:
        return "stale_submit"
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    limit = ctx.settings.llm.timeout_s + ctx.settings.fo.max_input_age_s
    return "stale_submit" if (ctx.now() - at).total_seconds() > limit else None


def _fo_account_done(
    ctx: FoToolContext, d: Path, stages: list[str], account_id: str, day: date, request_id: str
) -> bool:
    """아침 결정 재개 때 이 계좌를 건너뛸지(PR #61 리뷰·단계 4 리뷰). 판정 파일은 다리 예약
    **전에** 쓰이므로 그것만으로는 끝났다고 보지 않는다 — 보류 플래그·주문 체크포인트·**이 결정
    봉투(request_id)로 만든 원장 다리**(`fo_order_legs.request_id`)가 있거나, 판정이 실행할
    것이 없는 경우(거부·기록만·오류·건너뜀·다리 없음)만 끝난 것이다."""
    assert ctx.conn is not None
    if fo_pending_entry(ctx.conn, account_id, day) is not None:
        return True
    if f"_fo_order_{account_id}" in stages:
        return True
    if ctx.conn.execute(
        "SELECT 1 FROM fo_order_legs WHERE account_id = ? AND request_id = ? "
        "AND NOT (status = 'cancelled' AND order_no IS NULL AND sent_at IS NULL) LIMIT 1",
        (account_id, request_id),
    ).fetchone():
        return True
    rc_stage = f"_fo_risk_check_{account_id}"
    if rc_stage not in stages:
        return False
    rc = (read_checkpoint(d, rc_stage) or {}).get("data") or {}
    if rc.get("record_only") or rc.get("error") or rc.get("skipped"):
        return True
    if "intent" in rc and "verdict" not in rc:  # CLOSE·REPLACE 판정 — 축소 다리가 아직 없다
        return False
    verdict = rc.get("verdict")
    if verdict is None:  # 관망·거부 기록(`approved`·`legs` 를 직접 담은 형태)
        return not (rc.get("approved") and rc.get("legs"))
    return not (verdict.get("approved") and verdict.get("legs"))


def _fo_run_accounts(
    ctx: FoToolContext,
    *,
    date: date,
    env: Any,
    stage: str,
    inputs: dict,
    accounts: list[Any],
    record_only: str | None,
) -> dict:
    """아침 결정의 계좌 루프(제출·재개 공용). 계좌마다: 기록만(과거 asof·입력 날짜) → 관망 →
    진입 창 안이면 `run_leg`, 밖이면 보류 플래그(dry_run 은 보류하지 않고 기록만). **계좌 하나의
    예외는 그 계좌의 `_fo_risk_check_<계좌>` 에 error 로 남기고 다음 계좌로**
    (`OrderBookkeepingError` 는 CRITICAL + 알림 — 접수번호는 사람이 대조). 낡은 보류 플래그는 먼저
    지운다(S3 P0)."""
    fo = ctx.fo
    assert fo is not None and ctx.conn is not None
    dec = env.decision
    now = ctx.now()
    cfg = ctx.settings.fo
    executions: dict[str, list[dict]] = {}
    pending: list[str] = []
    notes: list[str] = []
    stale_note = {
        "historical": FO_STALE_NOTE, "input_date": FO_INPUT_DATE_NOTE,
        "stale_submit": FO_SUBMIT_STALE_NOTE,
    }.get(record_only or "")  # fmt: skip
    for a in accounts:
        rc_stage = f"_fo_risk_check_{a.id}"
        flag_key = f"{FO_PENDING_FLAG}{a.id}"
        if fo_pending_entry(ctx.conn, a.id, date) is None:
            control.set_flag(ctx.conn, flag_key, None)  # 낡은 보류 제거(소비자가 지웠을 수도)
        if stale_note:
            fo_exec._ckpt(
                fo, date, rc_stage,
                {"approved": False, "note": stale_note, "request_id": env.request_id,
                 "record_only": record_only, "reasons": [stale_note]},
            )  # fmt: skip
            continue
        try:
            has_pos = bool(fo_store.open_positions(ctx.conn, a.id))
            if dec.intent == "HOLD" and not has_pos:
                fo_exec._ckpt(
                    fo, date, rc_stage,
                    {"approved": True, "note": "관망(FLAT·HOLD, 보유 없음)",
                     "request_id": env.request_id, "reasons": [], "legs": []},
                )  # fmt: skip
                continue
            # 진입 창은 새 진입에만. 보유가 있는 RESIZE·REPLACE 는 축소·청산일 수 있어 장중이면
            # 바로 `run_leg` 로 보낸다(증액 다리는 게이트가 진입 창 밖이면 거부, PR #61 리뷰)
            entry = dec.intent == "OPEN" or (dec.intent in ("RESIZE", "REPLACE") and not has_pos)
            in_window = fo_rules.in_entry_window(now, cfg) if entry else is_fo_open(now)
            if in_window:
                executions[a.id] = fo_exec.run_leg(fo, a, env, round=0, source="decision")
            elif fo.dry_run:
                fo_exec._ckpt(
                    fo, date, rc_stage,
                    {"approved": False, "note": "진입 창 밖 — dry_run 이라 보류하지 않음",
                     "request_id": env.request_id, "record_only": "dry_run", "reasons": []},
                )  # fmt: skip
            else:
                control.set_flag(
                    ctx.conn, flag_key,
                    {"date": date.isoformat(), "stage": stage, "envelope": env.model_dump(),
                     "request_id": env.request_id, "requested_at": now.isoformat(),
                     "dry_run": False},
                )  # fmt: skip
                pending.append(a.id)
        except OrderBookkeepingError as e:
            logger.critical("파생 %s 주문 기록 실패(접수번호 %s): %s", a.id, e.order_no, e)
            fo_exec._notify(fo, f"[파생 주문 기록 실패] {a.id} 접수번호 {e.order_no or '-'}: {e}")
            executions[a.id] = [{"error": str(e), "order_no": e.order_no}]
            if rc_stage not in list_checkpoints(_fo_dir(ctx, date)):
                fo_exec._ckpt(
                    fo, date, rc_stage,
                    {"approved": False, "error": str(e), "order_no": e.order_no,
                     "request_id": env.request_id, "reasons": [str(e)]},
                )  # fmt: skip
        except Exception as e:  # noqa: BLE001 — 계좌 하나의 실패가 다른 계좌를 막지 않는다
            logger.exception("파생 %s 실행 실패: %s", a.id, e)
            err = f"{type(e).__name__}: {e}"
            fo_exec._ckpt(
                fo, date, rc_stage,
                {"approved": False, "error": err, "request_id": env.request_id, "reasons": [err]},
            )  # fmt: skip
            executions[a.id] = [{"error": err}]
    if stale_note:
        notes.append(stale_note)
    if pending:
        notes.append(f"진입 창 밖 — 보류 {', '.join(pending)}")
    executed = any("error" not in r for rs in executions.values() for r in rs)
    return {
        "executed": executed, "executions": executions, "pending": pending,
        "note": "; ".join(notes),
    }  # fmt: skip


# --- `trader tool` 명령 ---------------------------------------------------------------------

fo_tool_app = typer.Typer(help="파생 세션 도구 (Phase 26)", no_args_is_help=True)


def _open_ctx() -> tuple[FoToolContext, sqlite3.Connection | None]:
    """부트(포트 조립) → FoToolContext. 두 번째 값은 호출자가 닫을 연결(테스트가 바꿔 끼운다)."""
    from trader.kernel.cli.boot import boot

    b = boot(ports=True)
    return tool_ctx(b), b.conn


def _payload(json_text: str | None) -> dict:
    return json.loads(json_text if json_text is not None else sys.stdin.read())


def _fo_tool_run(fn: Callable[..., dict], *, market: str, **kwargs: Any) -> None:
    """파생 도구는 시장 `KR` 뿐 — 다른 값은 JSON 오류(S3 P2). 연결은 항상 닫는다."""
    if market != "KR":
        emit({"ok": False, "error": f"파생 도구의 --market 은 KR 뿐입니다: {market!r}"})
        return

    def go() -> dict:
        ctx, conn = _open_ctx()
        try:
            if isinstance(kwargs.get("date"), str):
                kwargs["date"] = date.fromisoformat(kwargs["date"])
            payload = kwargs.pop("payload_text", ...)
            if payload is not ...:
                kwargs["payload"] = _payload(payload)
            return fn(ctx, **kwargs)
        finally:
            if conn is not None:
                conn.close()

    run_tool(go, args={"market": market, **kwargs})


@fo_tool_app.command("bundle")
def bundle_cmd(
    market: str = typer.Option("KR", "--market"),
    date: str = typer.Option(..., "--date"),
    kind: str = typer.Option(..., "--kind", help="analyst | researcher | trader"),
    role: str | None = typer.Option(None, "--role", help="technical | sentiment | news_macro"),
    round: int | None = typer.Option(None, "--round"),
    side: str | None = typer.Option(None, "--side"),
    position_id: int | None = typer.Option(None, "--position-id", help="재평가 포지션 id"),
    account: str | None = typer.Option(None, "--account", help="재평가 계좌 id"),
    lane: str = typer.Option("a", "--lane", help="교차 토론 판 a | b"),
) -> None:
    """파생 서브에이전트 입력 묶음을 JSON 으로."""
    _fo_tool_run(
        fo_bundle, market=market, date=date, kind=kind, role=role, round=round, side=side,
        position_id=position_id, account=account, lane=lane,
    )  # fmt: skip


@fo_tool_app.command("write")
def write_cmd(
    market: str = typer.Option("KR", "--market"),
    date: str = typer.Option(..., "--date"),
    stage: str = typer.Option(
        ..., "--stage", help="_fo_analyst_<role> | _fo_debate_r<n>_<side> | _fo_debatex_r<n>_<side>"
    ),
    agent: str | None = typer.Option(None, "--agent"),
    json_text: str | None = typer.Option(None, "--json", help="JSON (없으면 stdin)"),
) -> None:
    """`_fo_analyst_<role>`·`_fo_debate_r<n>_<side>`·`_fo_debatex_r<n>_<side>`(2판) 저장.

    스키마·라운드를 검증한다."""
    _fo_tool_run(
        fo_write, market=market, date=date, stage=stage, payload_text=json_text, agent=agent
    )


def codex_turn(ctx: FoToolContext, *, date: date, round: int, side: str = "bear") -> dict:
    """고정 정책의 Codex `side` 턴(그 편이 Codex 인 판에 저장). 실제 호출 여부는 정책의
    `enabled` 가 정한다(`generate_turn`) — 기본 bear 는 옛 `cli_template` 호환."""
    if side not in ("bull", "bear"):
        return {"ok": False, "error": "--side 는 bull|bear 입니다"}
    d = _fo_dir(ctx, date)
    if len(_fo_reports(d)) < FO_MIN_ROLES:
        return {"ok": False, "error": "Codex 토론 전 서로 다른 애널리스트 2개 이상 필요"}
    return research_policy.generate_turn(ctx, d, RESEARCH, round, side=side)


@fo_tool_app.command("codex-turn")
def codex_turn_cmd(
    market: str = typer.Option("KR", "--market"),
    date: str = typer.Option(..., "--date"),
    round: int = typer.Option(..., "--round"),
    side: str = typer.Option("bear", "--side", help="Codex 편 bull | bear (기본 bear)"),
) -> None:
    """고정 정책의 Codex 턴(bull|bear) 호출·검증·증명 기록."""
    _fo_tool_run(codex_turn, market=market, date=date, round=round, side=side)


@fo_tool_app.command("summarize")
def summarize_cmd(
    market: str = typer.Option("KR", "--market"), date: str = typer.Option(..., "--date")
) -> None:
    """토론 턴 → `_fo_summary`."""
    _fo_tool_run(fo_summarize, market=market, date=date)


def submit_fo_decision_cmd(
    market: str = typer.Option("KR", "--market"),
    date: str = typer.Option(..., "--date"),
    stage: str | None = typer.Option(
        None, "--stage", help="재평가면 _fo_decision_r<n>_<계좌> (아침은 생략)"
    ),
    json_text: str | None = typer.Option(
        None, "--json", help="FoDecisionEnvelope JSON (없으면 stdin)"
    ),
) -> None:
    """파생 결정 제출 → 계좌별 판정·실행(주문은 fo 실행기에서만). JSON 1줄."""
    _fo_tool_run(submit_fo_decision, market=market, date=date, payload_text=json_text, stage=stage)
