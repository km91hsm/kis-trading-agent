"""scalp 판정자(scalp-judge): 틱 밖 스레드 풀·청산 파라미터 클램프·판정 입력·사후 요약·데몬
단일체. 틱은 submit 만 하고 다음 틱에 poll — 판정을 기다리느라 손절·청산 점검이 밀리지 않는다."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from trader.features.scalp import signals
from trader.features.scalp.fake import FAKE_ADVICE, FAKE_VERDICT
from trader.features.scalp.schemas import ScalpVerdict
from trader.features.scalp.settings import ScalpCfg
from trader.features.scalp.state import load_state, read_events
from trader.kernel.registry.types import AgentDef
from trader.kernel.session import quick, usage

logger = logging.getLogger("trader.scalp")

AGENT = AgentDef("scalp-judge", Path(__file__).parent / "agents" / "scalp-judge.md", ScalpVerdict)
JUDGE_TTL_S = 1800.0  # 회수되지 않은 판정 제출을 버리는 시간(초)


class ScalpJudge:
    """분 단위 LLM 판정을 틱 밖 스레드에서 돌린다. 데몬 재시작으로 future 가 사라지면 poll 은
    None(= 판정 없음 → 진입 안 함). ask(payload, schema) 는 `quick.ask_json` 을 감싼 함수.
    submit·poll·discard 는 틱 스레드 하나에서만 부른다(풀 스레드는 ask 만 돈다)."""

    def __init__(
        self,
        ask: Callable[[dict, type], Any],
        *,
        max_workers: int = 2,
        ttl_s: float = JUDGE_TTL_S,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._ask = ask
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="scalp-judge")
        self._futs: dict[str, Any] = {}
        self._at: dict[str, float] = {}  # 키별 제출 시각(만료 판단)
        self._ttl = ttl_s
        self._clock = clock or time.monotonic

    def _expire(self) -> None:
        """ttl 이 지난 제출은 회수 여부와 무관하게 버린다 — 신호가 났지만 다음 틱에 후보에서 빠진
        종목(회수되지 않은 키)이 하루 종일 쌓이지 않게. 진행 중이면 취소한다."""
        now = self._clock()
        for k in [k for k, at in self._at.items() if now - at >= self._ttl]:
            f = self._futs.pop(k, None)
            self._at.pop(k, None)
            if f is not None and not f.done():
                f.cancel()

    def submit(self, key: str, payload: dict, schema: type) -> None:
        self._expire()
        f = self._futs.get(key)
        if f is not None and not f.done():
            return
        self._futs[key] = self._pool.submit(self._ask, payload, schema)
        self._at[key] = self._clock()

    def poll(self, key: str) -> Any:
        """판정 결과 | "pending" | None(제출 기록 없음·실패·상한)."""
        f = self._futs.get(key)
        if f is None:
            return None
        if not f.done():
            return "pending"
        self._futs.pop(key, None)
        self._at.pop(key, None)
        try:
            return f.result()
        except Exception as e:  # noqa: BLE001 - 판정 실패는 '판정 없음'
            logger.warning("단타 판정 실패 %s: %s", key, e)
            return None

    def discard(self, key: str) -> None:
        """포지션이 닫히면 그 포지션의 남은 조언을 버린다(다음 포지션이 옛 결과를 읽지 않게)."""
        f = self._futs.pop(key, None)
        self._at.pop(key, None)
        if f is not None:
            f.cancel()

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


# --- 판정 입력·클램프 --------------------------------------------------------------------


def bars_json(bars: list, n: int = 20) -> list[dict]:
    """최근 n 개 1분봉을 JSON 친화 dict 로(가격은 문자열)."""
    return [
        {
            "time": c.time,
            "open": str(c.open),
            "high": str(c.high),
            "low": str(c.low),
            "close": str(c.close),
            "volume": c.volume,
        }
        for c in bars[-n:]
    ]


def judge_context(
    conn: Any,
    ports: dict[str, Any],
    market_client: Any,
    *,
    market: str,
    ticker: str,
    exchange: str | None,
    today: date,
) -> dict:
    """판정 입력의 공통 부분: 호가 잔량·국면·오늘 시장 판단 (실패한 절은 None). 뉴스는 레인이
    수집 기능을 읽지 않으므로 항상 빈 목록(A16)."""
    book = None
    try:
        ob = (
            market_client.get_orderbook_kr(ticker)
            if market == "KR"
            else market_client.get_orderbook_us(ticker, exchange)
        )
        book = {
            "asks": [[str(p), q] for p, q in ob.asks[:5]],
            "bids": [[str(p), q] for p, q in ob.bids[:5]],
        }
    except Exception as e:  # noqa: BLE001 - 호가는 보조 입력
        logger.debug("단타 호가 실패 %s: %s", ticker, e)
    regime = ports["regime"]
    try:
        reg = {
            "state": regime.state(conn, market),
            "count": regime.core_count(regime.load(conn, market)),
        }
    except Exception as e:  # noqa: BLE001 - 국면은 보조 입력
        logger.debug("단타 국면 실패 %s: %s", market, e)
        reg = None
    try:
        view = regime.view_today(conn, market, today)
    except Exception as e:  # noqa: BLE001 - 시장 판단은 보조 입력
        logger.debug("단타 시장 판단 실패 %s: %s", market, e)
        view = None
    return {
        "orderbook": book,
        "news": [],
        "regime": reg,
        "market_view": (
            {k: view.get(k) for k in ("stance", "confidence", "scalp_ok")} if view else None
        ),
    }


def limits(cfg: ScalpCfg) -> dict:
    return {
        "take_profit_pct": cfg.take_profit_pct,
        "stop_pct": cfg.stop_pct,
        "max_hold_min": cfg.max_hold_min,
    }


def clamp_verdict(v: Any, cfg: ScalpCfg) -> dict:
    """판정자의 청산 파라미터를 설정값 안으로: 손절은 넓힐 수 없고(≤ stop_pct) 보유도 늘릴 수 없다
    (≤ max_hold_min), 익절은 설정의 0.5~2배."""
    return {
        "take_profit_pct": min(
            max(v.take_profit_pct, cfg.take_profit_pct * 0.5), cfg.take_profit_pct * 2
        ),
        "stop_pct": min(v.stop_pct, cfg.stop_pct),
        "max_hold_min": min(v.max_hold_min, cfg.max_hold_min),
    }


# --- 사후 요약 ---------------------------------------------------------------------------


def hypothetical_return(
    bars: list, *, entry_price: Decimal, entry_time: str, cfg: ScalpCfg, close_hm: str
) -> float | None:
    """NO_GO 판정이 났던 신호를 규칙대로 샀다면의 수익(이후 1분봉으로 청산 규칙 재현). 봉이 모자라면
    마지막 봉 종가로. 신호 뒤 봉이 없으면 None."""
    after = [c for c in bars if (c.time or "") > entry_time]
    if not after or entry_price <= 0:
        return None
    for c in after:
        if signals.exit_reason(
            entry_price=entry_price,
            entry_time=entry_time,
            price=c.close,
            now_time=c.time or "",
            cfg=cfg,
            close_hm=close_hm,
        ):
            return float(c.close / entry_price - 1)
    return float(after[-1].close / entry_price - 1)


def _bars_for(minutes_fn: Callable, ticker: str, exchange: str | None) -> list:
    """minutes_fn(ticker[, exchange]) — 판정 사건에 남긴 거래소를 넘길 수 있으면 넘긴다."""
    try:
        return minutes_fn(ticker, exchange)
    except TypeError:
        return minutes_fn(ticker)


def judge_summary(
    state: Path | None,
    *,
    market: str,
    days: list[date],
    cfg: ScalpCfg,
    close_hm: str = "15:30",
    minutes_fn: Callable[[str], list] | None = None,
    today: date | None = None,
) -> dict:
    """판정자 사후 비교: GO 는 실제 청산 수익, NO_GO 는 오늘분만 `minutes_fn`(당일 분봉) 으로 가상
    수익. 판정 없음(시간 초과·재시작)은 건수만."""
    go_rets: list[float] = []
    nogo_rets: list[float] = []
    counts = {"go": 0, "no_go": 0, "none": 0}
    for d in days:
        events = read_events(state, d)
        if not events:
            continue
        closed = load_state(state, d, market).get("closed", [])
        judged = {c["ticker"]: c for c in closed if c.get("judged")}
        for e in events:
            if e.get("kind") != "judge" or e.get("market") != market:
                continue
            if e.get("go") is None:
                counts["none"] += 1
            elif e["go"]:
                counts["go"] += 1
                if e["ticker"] in judged:
                    go_rets.append(float(judged[e["ticker"]]["ret"]))
            else:
                counts["no_go"] += 1
                if minutes_fn is not None and d == today:
                    try:
                        r = hypothetical_return(
                            _bars_for(minutes_fn, e["ticker"], e.get("exchange")),
                            entry_price=Decimal(str(e["signal_price"])),
                            entry_time=str(e["signal_time"]),
                            cfg=cfg,
                            close_hm=close_hm,
                        )
                    except Exception:  # noqa: BLE001 - 분봉 실패는 건너뜀
                        r = None
                    if r is not None:
                        nogo_rets.append(r)

    def avg(xs: list[float]) -> float | None:
        return round(sum(xs) / len(xs), 5) if xs else None

    return {
        "counts": counts,
        "go_avg_ret": avg(go_rets),
        "go_trades": len(go_rets),
        "no_go_avg_ret": avg(nogo_rets),
        "no_go_measured": len(nogo_rets),
    }


# --- 데몬 단일체 -------------------------------------------------------------------------

_JUDGE: ScalpJudge | None = None


def judge_for(deps: Any) -> ScalpJudge:
    """판정자 스레드 풀은 데몬 수명 동안 하나(재시작이면 제출 중 판정은 버려진다 = 진입 안 함).
    판정 스레드는 원장 연결을 따로 연다(SQLite 연결은 스레드 사이에 공유하지 않는다)."""
    global _JUDGE
    if _JUDGE is None:
        cfg = deps.settings.scalp

        def ask(payload: dict, schema: type) -> Any:
            if deps.stop_event.is_set():  # 종료 중: 대기열에 남은 판정은 띄우지 않는다
                return None
            conn = deps.conn_factory()
            try:
                ok, reason = usage.session_allowance(
                    conn, deps.settings, kind="scalp_judge", now=deps.now()
                )
                if not ok:
                    logger.info("단타 판정 생략: %s", reason)
                    return None
                return quick.ask_json(
                    deps.settings,
                    conn,
                    agent=AGENT,
                    payload=payload,
                    schema=schema,
                    cap=cfg.judge_daily_cap,
                    timeout_s=cfg.judge_timeout_s,
                    now=deps.now,
                    cancel=deps.stop_event,  # 종료 요청이면 실행 중인 claude 도 정리
                    fake=lambda: FAKE_VERDICT if schema is ScalpVerdict else FAKE_ADVICE,
                )
            finally:
                conn.close()

        _JUDGE = ScalpJudge(ask)
    return _JUDGE


def shutdown_judge() -> None:
    """전역 판정자 풀을 닫고 비운다(데몬 종료·테스트 정리)."""
    global _JUDGE
    if _JUDGE is not None:
        _JUDGE.shutdown()
    _JUDGE = None
