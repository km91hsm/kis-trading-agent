"""evaluation 포트: 레인이 `Boot.ports["evaluation"]` 로 받는 판단 기억·보정·서킷브레이커
(커널 `execution.ports.EvaluationPort`). RL 사이저 `rl_*` 포함."""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from trader.features.evaluation import calibration
from trader.features.evaluation.memory import store
from trader.features.evaluation.memory import vector as vec
from trader.features.evaluation.rl import shadow as rl_shadow
from trader.features.ml import api as ml_api
from trader.kernel.config import KST
from trader.kernel.config.market_hours import MARKET_CLOSE
from trader.kernel.execution import ledger as xledger
from trader.kernel.registry.types import Port
from trader.kernel.session.run_store import run_date_for

logger = logging.getLogger("trader.evaluation")


def _record_vix(conn: sqlite3.Connection, inputs: dict) -> None:
    """00_inputs 의 news_macro.macro.vix(FRED 최신 관측)를 기록한다 (VIX 급등 브레이커 입력)."""
    try:
        vix = ((inputs.get("news_macro") or {}).get("macro") or {}).get("vix") or {}
        data = vix.get("data") or {}
        if data.get("value") is not None and data.get("date"):
            store.record_vix(conn, value=float(data["value"]), date=str(data["date"]))
    except Exception as e:  # noqa: BLE001 - VIX 기록 실패는 메모리 검색을 막지 않는다
        logger.warning("VIX 기록 실패: %s", e)


def _decided_at(now: datetime, run_date: date, market: str = "KR") -> datetime:
    """운영일이 지금의 운영일이면 현재 시각, 아니면 그날 장 마감(15:30 KST) 시각(UTC).

    운영일은 시장 기준(`run_date_for`) — US 는 KST 자정을 넘긴 장중 재평가도 같은 운영일이라
    현재 시각을 써야 라운드 순서(체결 귀속 창)가 뒤집히지 않는다."""
    if run_date_for(market, now) == run_date:
        return now
    return datetime.combine(run_date, MARKET_CLOSE["KR"], KST).astimezone(UTC)


class EvaluationPortImpl:
    def __init__(self, settings: Any, registry: Any = None, ports: Any = None) -> None:
        self._settings = settings
        self._registry = registry
        self._ports = ports if ports is not None else {}  # 생성 뒤에 채워지므로 지연 해석

    def _latest_row(self, ticker: str, market: str, asof: date | None) -> dict | None:
        ml = self._ports.get("ml")
        if ml is None or not ticker:
            return None
        return ml.latest_row(ticker, market, asof=asof)

    def memory_context(
        self,
        conn: sqlite3.Connection,
        *,
        inputs: dict,
        market: str,
        asof_date: date,
        now: datetime,
        state: Path | None,
    ) -> dict:
        """{memory: [...상위 k 요약], memory_status: {warmup, breaker}}. 실패하면 빈 목록."""
        try:
            _record_vix(conn, inputs)
            ticker = str((inputs.get("meta") or {}).get("ticker") or "")
            row = self._latest_row(ticker, market, asof_date)
            qv = vec.situation_vector(row or {}, market)
            r = store.retrieve(
                conn, vector=qv, now=now, settings=self._settings.memory, market=market
            )
        except Exception as e:  # noqa: BLE001 - 메모리는 선택 입력
            logger.warning("메모리 검색 실패: %s", e)
            return {
                "memory": [],
                "memory_status": {"warmup": True, "breaker": None, "error": str(e)},
            }
        entries = [
            {
                "id": e["id"],
                "ticker": e["ticker"],
                "decided_at": e["decided_at"],
                "situation_summary": e["situation_summary"],
                "decision": e["decision"],
                "round": e["round"],
                "direction_score": e["direction_score"],
                "avoided_loss": e["avoided_loss"],
                "realized_pnl_pct": e["realized_pnl_pct"],
                "post_fill_return": e.get("post_fill_return"),
                "lesson": (e.get("reflection") or {}).get("lesson")
                if e.get("reflection")
                else None,
                "score": e["score"],
            }
            for e in r["entries"]
        ]
        status = {"warmup": r["warmup"], "breaker": r["breaker"]}
        return {"memory": entries, "memory_status": status}

    def calibration_injected(self) -> bool:
        """`ml.yaml calibration.enabled ∧ inject`(결정 14번). 설정을 못 읽으면 넣지 않는다."""
        try:
            cfg = ml_api.load_ml(self._registry).models.calibration
            return bool(cfg.enabled and cfg.inject)
        except Exception:  # noqa: BLE001
            return False

    def calibration_report(
        self,
        conn: sqlite3.Connection,
        *,
        ticker: str,
        market: str,
        asof_date: date,
        state: Path | None,
        now_iso: str,
    ) -> dict | None:
        """주입이 켜졌고 결과 엔트리가 min_decisions 이상일 때만 리포트(체크포인트 쓰기는 레인)."""
        if not self.calibration_injected():
            return None
        try:
            cfg = ml_api.load_ml(self._registry).models.calibration
            row = self._latest_row(ticker, market, asof_date)
            qv = vec.situation_vector(row, market) if row else None
            return calibration.build_calibration(
                conn, min_decisions=cfg.min_decisions, query_vector=qv, asof=now_iso
            )
        except Exception as e:  # noqa: BLE001 - 보정은 선택 입력
            logger.warning("35_calibration 생략 %s %s: %s", market, ticker, e)
            return None

    @staticmethod
    def _held_at_decision(
        conn: sqlite3.Connection, accounts: list, market: str, ticker: str, d: date
    ) -> tuple[str, bool | None]:
        """(엔트리 계좌, 결정 당시 보유). 그 시장의 모의 계좌 중 하나라도 보유하면 보유이고 그
        계좌를 엔트리 계좌로 쓴다. 모두 스냅샷이 없으면 None(모름), 아니면 False — 엔트리 계좌는
        첫 모의 계좌. 모의 계좌가 없으면 첫 계좌."""
        papers = [a.id for a in accounts if a.env == "paper" and market in a.markets]
        papers = papers or [a.id for a in accounts if a.env == "paper"]
        papers = papers or [a.id for a in accounts[:1]]
        if not papers:
            return "", None
        held: bool | None = None
        for acct in papers:
            h = store.held_at(conn, account_id=acct, market=market, ticker=ticker, d=d.isoformat())
            if h:
                return acct, True
            if h is False:
                held = False
        return papers[0], held

    def record_decision(
        self,
        conn: sqlite3.Connection,
        *,
        decision: dict,
        ticker: str,
        market: str,
        date: date,
        round_no: int,
        now: datetime,
        state: Path | None,
        accounts: list,
        skipped_debate: bool | None,
    ) -> int | None:
        """결정 1건(라운드별)을 메모리 엔트리로 기록한다 ((decision_id, round) 멱등). 실패해도
        결정 제출을 막지 않는다."""
        try:
            decision_id = xledger.decision_id(conn, date, market, ticker)
            if decision_id is None:
                return None
            paper, held = self._held_at_decision(conn, accounts, market, ticker, date)
            return store.record_decision(
                conn,
                decision_id=decision_id,
                ticker=ticker,
                market=market,
                account_id=paper,
                decided_at=_decided_at(now, date, market).isoformat(),
                decision=decision,
                row=self._latest_row(ticker, market, date),
                horizon_days=decision["horizon_days"],
                round_no=round_no,
                held_at_decision=held,
                skipped_debate=skipped_debate,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("메모리 기록 실패 %s %s: %s", market, ticker, e)
            return None

    def update_breaker(
        self, conn: sqlite3.Connection, *, market: str, nav_series: list[tuple[str, Decimal]]
    ) -> dict | None:
        """모의 계좌 NAV 계열·최근 VIX 변화로 그 시장의 메모리 서킷브레이커를 갱신한다."""
        try:
            return store.circuit_breaker(
                conn,
                nav_series=nav_series,
                vix_change=store.latest_vix_change(conn),
                settings=self._settings.memory,
                market=market,
            )
        except Exception as e:  # noqa: BLE001 - 브레이커 갱신 실패는 판정을 막지 않는다
            logger.warning("서킷브레이커 갱신 실패 %s: %s", market, e)
            return None

    def rl_sizer(self, state: Path | None) -> Any | None:
        """승격된 RL 사이저(`ml.yaml rl_sizer.enabled` 일 때만). 로드 실패는 경고 후 None."""
        try:
            return rl_shadow.load(ml_api.load_ml(self._registry), state)
        except Exception as e:  # noqa: BLE001 - 사이저는 선택 기능
            logger.warning("RL 사이저 로드 건너뜀: %s", e)
            return None

    def rl_propose(
        self,
        sizer: Any,
        *,
        state: Path | None,
        ticker: str,
        market: str,
        decision: dict,
        portfolio: Any,
        nav_hist: list[tuple[str, Decimal]],
        base: list[float] | None,
    ) -> tuple[list[float], float, list[float]]:
        """(관측 16차원, 제안 비중, 기준 14차원). base 를 주면 피처 스토어를 다시 읽지 않는다."""
        base = base or rl_shadow.base_observation(
            state, ticker=ticker, market=market, decision=decision
        )
        obs = rl_shadow.observe(
            state,
            ticker=ticker,
            market=market,
            decision=decision,
            portfolio=portfolio,
            nav_hist=nav_hist,
            base=base,
        )
        return obs, rl_shadow.propose(sizer, obs), base

    def rl_record(
        self,
        conn: sqlite3.Connection,
        *,
        decision_id: int,
        account_id: str,
        model_version: str,
        obs: list[float],
        llm_weight: float,
        proposed_weight: float,
        applied: bool,
    ) -> int:
        return rl_shadow.record(
            conn,
            decision_id=decision_id,
            account_id=account_id,
            model_version=model_version,
            obs=obs,
            llm_weight=llm_weight,
            proposed_weight=proposed_weight,
            applied=applied,
        )


def build_port(boot: Any) -> EvaluationPortImpl:
    return EvaluationPortImpl(
        boot.settings, getattr(boot, "registry", None), getattr(boot, "ports", None)
    )


PORT = Port("evaluation", build_port)
