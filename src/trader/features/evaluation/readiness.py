"""실전 전환 준비도 점검 (스펙 §11 Phase 8 완료 기준). 읽기 전용: 모드를 바꾸지 않는다.

항목: 모의 운영일(FAKE 제외 analyze ok 날짜 수) ≥ 20, 28일 runs 오류율 ≤ 10%, 주문 실패율 ≤ 10%,
LLM 알파(섀도 포트폴리오 llm − 같은 날 후보 1/N, 만기 쌍 ≥ 20 이고 95% 신뢰구간 하한 > 0),
MDD ≥ −10%, 보정 활성(정보), 만료된 미결 승인 0건. 30일 수익률 − 유니버스 1/N 은 정보로만 남긴다
(계좌 NAV 는 대부분 현금이라 시장 베타와 섞여 선택 능력을 재지 못한다 — 전체 검토 L1).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from trader.features.evaluation import performance, shadow
from trader.features.evaluation.memory import store
from trader.kernel.config import KST
from trader.kernel.session.agents import ORCHESTRATOR

MIN_PAPER_DAYS = 20
MAX_ERROR_RATE = 0.10
MAX_FAILED_RATE = 0.10
MIN_MDD = -0.10
WINDOW_DAYS = 28
DEFAULT_MIN_DECISIONS = 60
MIN_SHADOW_PAIRS = 20


def _item(key: str, ok: bool, value, threshold, note: str = "") -> dict:
    return {"key": key, "ok": bool(ok), "value": value, "threshold": threshold, "note": note}


def interval_kinds_of(features: Iterable[Any]) -> frozenset[str]:
    """interval 잡의 `runs.kind` 후보(잡 kind 와 id; `{market}` 은 kr/us 로 전개).

    interval 잡은 오류일 때만 runs 에 남아 오류율 분모를 왜곡하므로 점검에서 제외한다."""
    out: set[str] = set()
    for f in features:
        for j in f.jobs:
            if j.trigger != "interval":
                continue
            out.add(j.kind)
            for m in ("kr", "us") if "{market}" in j.id else ("",):
                out.add(j.id.replace("{market}", m))
    return frozenset(out)


def _paper_days(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        "SELECT r.started_at FROM runs r WHERE r.kind='analyze' AND r.status='ok' AND EXISTS ("
        "SELECT 1 FROM agent_calls a WHERE a.run_id=r.id AND a.agent=? "
        "AND a.model IS NOT NULL AND a.model != 'fake')",
        (ORCHESTRATOR,),
    ).fetchall()
    days = set()
    for r in rows:
        try:
            days.add(datetime.fromisoformat(r["started_at"]).astimezone(KST).date())
        except ValueError:
            continue
    return len(days)


def _rate(
    conn: sqlite3.Connection,
    table: str,
    col: str,
    bad: tuple,
    since_iso: str,
    interval_kinds: frozenset[str] = frozenset(),
) -> float:
    marks = ",".join("?" * len(bad))
    extra, args = "", []
    if table == "runs":
        # 오류만 기록되는 interval 잡 제외. 재평가 세션(run_session kind='monitor')은 ok 도
        # 기록되니 interval_kinds 에 넣지 않으면 포함된다. 상태는 ok/error 만 센다(running 제외).
        if interval_kinds:
            kinds = sorted(interval_kinds)
            extra = f" AND kind NOT IN ({','.join('?' * len(kinds))})"
            args = kinds
        extra += " AND status IN ('ok','error')"
    elif table == "orders":
        extra = " AND asset = 'stock'"  # 파생 레인 주문은 현물 준비도에 섞지 않는다
    row = conn.execute(
        f"SELECT COUNT(*) AS n, SUM(CASE WHEN status IN ({marks}) THEN 1 ELSE 0 END) AS b "
        f"FROM {table} WHERE {col} >= ?{extra}",
        (*bad, since_iso, *args),
    ).fetchone()
    return (row["b"] or 0) / row["n"] if row["n"] else 0.0


def check(
    conn: sqlite3.Connection,
    *,
    accounts: list,
    state: Path | None,
    today: date,
    interval_kinds: frozenset[str] = frozenset(),
    calibration: Any | None = None,
) -> dict:
    # 승인 만료는 실제 현재 시각(오늘 자정 이전) 기준: 아직 유효한 당일 승인은 stale 이 아니다
    now = min(datetime.now(UTC), datetime.combine(today, time.max, UTC))
    since_iso = (now - timedelta(days=WINDOW_DAYS)).isoformat()
    items: list[dict] = []

    n_days = _paper_days(conn)
    items.append(_item("paper_days", n_days >= MIN_PAPER_DAYS, n_days, MIN_PAPER_DAYS))
    er = _rate(conn, "runs", "started_at", ("error",), since_iso, interval_kinds)
    items.append(_item("run_error_rate", er <= MAX_ERROR_RATE, round(er, 4), MAX_ERROR_RATE))
    fr = _rate(conn, "orders", "created_at", ("failed", "rejected"), since_iso)
    items.append(_item("order_failed_rate", fr <= MAX_FAILED_RATE, round(fr, 4), MAX_FAILED_RATE))

    excess: list[float] = []
    mdds: list[float] = []
    notes: list[str] = []
    for a in accounts:
        if a.env != "paper" or getattr(a, "asset", "stock") != "stock":
            continue  # 파생 계좌(Phase 26)는 실전 전환 준비도 대상이 아니다
        for m in a.markets:
            series = performance.nav_series(conn, a.id, m, days=30)
            met = performance.metrics_of(series)
            if met["return"] is None:
                continue
            start, end = date.fromisoformat(met["start"]), date.fromisoformat(met["end"])
            bench = performance.benchmarks(state, m, start, end)["one_over_n"]["return"]
            if bench is None:
                notes.append(f"{a.id}/{m}: 벤치마크 없음(피처 스토어 종가 없음) → 0 대비")
                bench = 0.0
            excess.append(round(met["return"] - bench, 6))
            mdds.append(met["mdd"])
            notes.append(f"{a.id}/{m}: 수익률 {met['return']:+.2%} vs 1/N {bench:+.2%}")
    if excess:
        items.append(
            _item("return_vs_benchmark", True, min(excess), 0.0, "정보: " + "; ".join(notes))
        )
        items.append(_item("mdd", min(mdds) >= MIN_MDD, min(mdds), MIN_MDD))
    else:
        note = "정보: NAV 스냅샷 2일 이상 필요"
        items.append(_item("return_vs_benchmark", True, None, 0.0, note))
        items.append(_item("mdd", False, None, MIN_MDD, "NAV 스냅샷 2일 이상 필요"))

    # 2-L5: 시장을 이어 붙인 부트스트랩은 동시 시장 상관을 무시한다 — 실전 계좌가 거래하는(없으면
    # 모의 계좌의) 시장마다 따로 20쌍 ∧ 하한 > 0. 현금인 날은 쌍에서 빠지고 현금 효과는 정보로(2-L3)
    real = sorted({m for a in accounts if a.env == "real" for m in a.markets})
    markets = real or sorted({m for a in accounts for m in a.markets}) or ["KR"]
    oks: list[bool] = []
    lows: list[float] = []
    notes_a: list[str] = []
    for m in markets:
        ps = shadow.paired_stats(conn, market=m)
        ok_m = ps["n"] >= MIN_SHADOW_PAIRS and (ps["ci_low"] or 0) > 0
        oks.append(ok_m)
        if ps["ci_low"] is not None:
            lows.append(ps["ci_low"])
        ce = shadow.cash_effect(conn, market=m)
        detail = (
            "-"
            if not ps["n"]
            else f"평균 {ps['mean_diff']:+.2%}, 구간 없음(쌍 부족)"
            if ps["ci_low"] is None
            else (
                f"평균 {ps['mean_diff']:+.2%}, 95% 구간 [{ps['ci_low']:+.2%}, {ps['ci_high']:+.2%}]"
            )
        )
        cash = f", 현금일 {ce['n']}일 효과 {ce['mean']:+.2%}" if ce["n"] else ""
        notes_a.append(f"{m} {ps['n']}쌍 {detail}{cash}")
    items.append(
        _item(
            "llm_alpha",
            bool(oks) and all(oks),
            min(lows) if lows else None,
            0.0,
            f"섀도 llm − 후보 1/N(공통 후보·현금일 제외, 시장별 기준 {MIN_SHADOW_PAIRS}쌍): "
            + "; ".join(notes_a),
        )
    )

    min_dec = int(calibration.min_decisions) if calibration is not None else DEFAULT_MIN_DECISIONS
    inject = bool(calibration.inject) if calibration is not None else False
    matured = store.matured_count(conn)
    items.append(
        _item(
            "calibration_active",
            True,
            matured >= min_dec,
            min_dec,
            f"정보: 만기 결정 {matured}건 (활성 기준 {min_dec}"
            + ("" if inject else ", 트레이더 주입 꺼짐 — L12 개선 전")
            + ")",
        )
    )
    stale = conn.execute(
        "SELECT COUNT(*) FROM approvals WHERE status='pending' AND expires_at < ?",
        (now.isoformat(),),
    ).fetchone()[0]
    items.append(_item("stale_approvals", stale == 0, int(stale), 0))

    return {"today": today.isoformat(), "ok": all(i["ok"] for i in items), "items": items}
