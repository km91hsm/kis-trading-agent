"""spot 결정·주문 표: merge 조각 행(`decision_rows`·`order_rows`), 결정 상세·trace·outcomes·
체크포인트, news-refs, 시장 today, risk-checks, SSE 지문. 원장·체크포인트 파일만 읽는다(조회 전용).

옛 `web/routers/runs.py`(`today`·`market_checkpoint`·`checkpoint`·`decision_trace`·
`decision_outcomes`)·`routers/orders.py`·`ledger.views` 이식. trace 는 옛 체크포인트 요약 대신
원장 표(`analyst_reports`·`debate_turns`·`risk_checks`·`orders`·`decision_rounds`)를 읽고, 원장에
없는 것(리서치 요약·현재가·재평가/손절/재호가 게이트 단계)과 체크포인트 목록만 run 디렉터리에서.

거래일(`date`)은 KST 운영일 — 결정 `date` 와 같은 축(`run_store.run_date_for`). 주문은 그 날의
시장 현지 하루(`ledger.day_bounds`). `date` 가 없으면 시장마다 오늘 운영일."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from trader.features.spot import approvals
from trader.features.spot.tools import validate_ids
from trader.kernel.config import KST
from trader.kernel.execution import ledger as xledger
from trader.kernel.ledger import names
from trader.kernel.platform import paths
from trader.kernel.session import run_store

MARKETS = ("KR", "US")
BUY_ACTIONS = ("BUY", "ADD")
SELL_ACTIONS = ("SELL", "TRIM")
NEWS_REF = re.compile(r"news:([0-9]+)")
RISK_CHECKS_LIMIT = 500  # /risk-checks 한 번에 돌려주는 판정 수(날짜 조건 뒤에 적용)
MARKET_STAGE = re.compile(r"^_[a-z0-9_]{1,40}$")
ROLE_LABEL = {
    "technical": "차트", "fundamental": "회사가치", "news_macro": "뉴스·거시",
    "sentiment": "투자자 분위기",
}  # fmt: skip
RESEARCH_KEYS = (
    "rounds", "gap_round0", "gap_final", "sycophantic", "preferred", "skipped_debate", "reason",
)  # fmt: skip


# --- 공통 ---------------------------------------------------------------------------------


def parse_date(value: str | None) -> date | None:
    """`YYYY-MM-DD` → date (빈 값 None). 형식 오류는 ValueError."""
    return date.fromisoformat(value) if value else None


def days_by_market(q: dict[str, str], now: Any) -> dict[str, date]:
    """조회할 시장 → 거래일. `market` 이 있으면 그 시장만(모르는 시장이면 빈 dict), `date` 가
    없으면 시장마다 오늘 운영일."""
    m = (q.get("market") or "").upper()
    markets = [m] if m else list(MARKETS)
    d = parse_date(q.get("date"))
    return {x: d or run_store.run_date_for(x, now) for x in markets if x in MARKETS}


def side_of(action: str | None) -> str | None:
    """결정 action → 주문 방향. HOLD 등은 None."""
    if action in BUY_ACTIONS:
        return "BUY"
    if action in SELL_ACTIONS:
        return "SELL"
    return None


def _loads(text: str | None, default: Any) -> Any:
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default


def _hydrate(row: sqlite3.Row) -> dict:
    out = dict(row)
    out["data"] = _loads(out.pop("data_json", None), {})
    return out


def _run_path(state: Path | None, d: str, market: str, ticker: str) -> Path:
    """run 디렉터리 경로만 조립한다(`run_store.run_dir` 은 디렉터리를 만든다 — 조회는 안 만듦)."""
    base = state if state is not None else paths.state_dir()
    return base / "runs" / d / market / ticker


def _stages(rd: Path) -> list[str]:
    return run_store.list_checkpoints(rd) if rd.is_dir() else []


def _data(rd: Path, stage: str) -> dict:
    cp = (run_store.read_checkpoint(rd, stage) if rd.is_dir() else None) or {}
    return cp.get("data") if isinstance(cp.get("data"), dict) else {}


# --- merge 조각: /decisions ----------------------------------------------------------------


def _order_state(conn: sqlite3.Connection, did: int, account_id: str | None) -> dict:
    """결정의 qty·status·account_id: 마지막 주문 → 승인 대기 → 판정 거부(`blocked`) → 없음."""
    acct_sql, args = (" AND account_id=?", [account_id]) if account_id else ("", [])
    o = conn.execute(
        f"SELECT account_id, qty, status FROM orders WHERE decision_id=?{acct_sql} "
        "ORDER BY id DESC LIMIT 1",
        [did, *args],
    ).fetchone()
    if o is not None:
        return {"account_id": o["account_id"], "qty": o["qty"], "status": o["status"]}
    a = conn.execute(
        f"SELECT account_id, order_json FROM approvals WHERE decision_id=? AND status='pending'"
        f"{acct_sql} ORDER BY id DESC LIMIT 1",
        [did, *args],
    ).fetchone()
    if a is not None:
        qty = _loads(a["order_json"], {}).get("qty")
        return {"account_id": a["account_id"], "qty": qty, "status": "pending_approval"}
    rc = conn.execute(
        f"SELECT account_id, adjusted_qty, approved FROM risk_checks WHERE decision_id=?"
        f"{acct_sql} ORDER BY id DESC LIMIT 1",
        [did, *args],
    ).fetchone()
    if rc is not None and not rc["approved"]:
        return {"account_id": rc["account_id"], "qty": rc["adjusted_qty"], "status": "blocked"}
    return {"account_id": account_id, "qty": None, "status": None}


def _touches(conn: sqlite3.Connection, did: int, account_id: str) -> bool:
    """결정이 그 계좌에 주문·승인·판정 흔적을 남겼는가(계좌 필터)."""
    return any(
        conn.execute(f"SELECT 1 FROM {t} WHERE decision_id=? AND account_id=? LIMIT 1",
                     (did, account_id)).fetchone()  # fmt: skip
        for t in ("orders", "approvals", "risk_checks")
    )


def _decision_row(conn: sqlite3.Connection, r: sqlite3.Row, account_id: str | None) -> dict:
    return {
        "asset": "spot", "id": r["id"], "date": r["date"], "market": r["market"],
        "ticker": r["ticker"], "name": names.resolve(conn, r["market"], r["ticker"]),
        "side": side_of(r["action"]),
        **_order_state(conn, r["id"], account_id),
        "at": r["created_at"], "lane": "spot", "action": r["action"],
        "summary": r["rationale"] or None,
        "target_weight": r["target_weight"], "confidence": r["confidence"],
        "source": r["source"],
    }  # fmt: skip


def _scalp_rows(conn: sqlite3.Connection, m: str, d: date, account_id: str | None) -> list[dict]:
    """단타는 결정 행을 만들지 않는다(`scalp.engine`) — 그날 `origin='scalp'` 주문을 결정 목록의
    `lane: scalp` 행으로. id 는 `scalp-<주문 id>`(현물 결정 상세 경로와 섞이지 않게)."""
    lo, hi = xledger.day_bounds(d, m)
    acct_sql, args = (" AND account_id=?", [account_id]) if account_id else ("", [])
    rows = conn.execute(
        "SELECT id, account_id, ticker, side, qty, status, created_at FROM orders "
        "WHERE origin='scalp' AND asset='stock' AND market=? AND created_at >= ? "
        f"AND created_at < ?{acct_sql} ORDER BY id",
        [m, lo, hi, *args],
    ).fetchall()
    return [
        {
            "asset": "spot", "id": f"scalp-{r['id']}", "date": d.isoformat(), "market": m,
            "ticker": r["ticker"], "name": names.resolve(conn, m, r["ticker"]),
            "side": r["side"], "account_id": r["account_id"],
            "qty": r["qty"], "status": r["status"], "at": r["created_at"], "lane": "scalp",
            "action": r["side"], "order_id": r["id"], "summary": None,
        }  # fmt: skip
        for r in rows
    ]


def decision_rows(deps: Any, q: dict[str, str]) -> list[dict]:
    """`ResourcePart("/decisions")` 현물 조각(단타 포함). 쿼리 `date`·`market`·`account_id`."""
    account_id = q.get("account_id") or None
    out: list[dict] = []
    with closing(deps.conn_factory()) as conn:
        for m, d in days_by_market(q, deps.now()).items():
            for r in conn.execute(
                "SELECT * FROM decisions WHERE date=? AND market=? ORDER BY id", (d.isoformat(), m)
            ).fetchall():
                if account_id and not _touches(conn, r["id"], account_id):
                    continue
                out.append(_decision_row(conn, r, account_id))
            out += _scalp_rows(conn, m, d, account_id)
    return out


# --- merge 조각: /orders -------------------------------------------------------------------


def order_rows(deps: Any, q: dict[str, str]) -> list[dict]:
    """`ResourcePart("/orders")` 현물 조각: `orders`(asset='stock') + 체결. 쿼리 `date`·
    `account_id`·`status`(쉼표로 여럿)·`market`. 단타는 `origin='scalp'` → `lane: scalp`."""
    account_id = q.get("account_id") or None
    statuses = [s for s in (q.get("status") or "").split(",") if s]
    out: list[dict] = []
    with closing(deps.conn_factory()) as conn:
        for m, d in days_by_market(q, deps.now()).items():
            lo, hi = xledger.day_bounds(d, m)
            sql = ("SELECT * FROM orders WHERE asset='stock' AND market=? AND created_at >= ? "
                   "AND created_at < ?")  # fmt: skip
            args: list[Any] = [m, lo, hi]
            if account_id:
                sql += " AND account_id=?"
                args.append(account_id)
            if statuses:
                sql += f" AND status IN ({','.join('?' * len(statuses))})"
                args += statuses
            for r in conn.execute(sql + " ORDER BY id", args).fetchall():
                fills = [
                    dict(f) for f in conn.execute(
                        "SELECT qty, price, filled_at FROM fills WHERE order_id=? ORDER BY id",
                        (r["id"],),
                    )
                ]  # fmt: skip
                out.append({
                    "asset": "spot", "id": r["id"], "account_id": r["account_id"],
                    "market": r["market"], "ticker": r["ticker"],
                    "name": names.resolve(conn, r["market"], r["ticker"]), "side": r["side"],
                    "qty": r["qty"], "price": r["price"], "order_type": r["order_type"],
                    "status": r["status"], "order_no": r["order_no"], "origin": r["origin"],
                    "lane": "scalp" if r["origin"] == "scalp" else "spot",
                    "decision_id": r["decision_id"], "filled_qty": r["filled_qty"],
                    "amount": r["amount"], "mode": r["mode"], "message": r["message"],
                    "fills": fills, "at": r["created_at"],
                })  # fmt: skip
    return out


# --- 상세·trace·outcomes·체크포인트 --------------------------------------------------------


def get_decision(conn: sqlite3.Connection, decision_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM decisions WHERE id=?", (decision_id,)).fetchone()
    return _hydrate(row) if row is not None else None


def _reports(conn: sqlite3.Connection, dec: dict) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM analyst_reports WHERE decision_date=? AND market=? AND ticker=? "
        "ORDER BY role",
        (dec["date"], dec["market"], dec["ticker"]),
    ).fetchall()


def news_ids_of(data: dict) -> list[int]:
    """리포트 evidence 중 `source_id` 가 `news:<id>` 인 뉴스 id."""
    out = []
    for e in data.get("evidence") or []:
        m = NEWS_REF.fullmatch(str(e.get("source_id") or "")) if isinstance(e, dict) else None
        if m:
            out.append(int(m.group(1)))
    return out


def news_refs(conn: sqlite3.Connection, dec: dict) -> list[int]:
    """결정이 쓴 뉴스 id(중복 없이 오름차순)."""
    return sorted({n for r in _reports(conn, dec) for n in news_ids_of(_loads(r["data_json"], {}))})


def news_refs_map(conn: sqlite3.Connection, days: dict[str, date]) -> dict[str, list[int]]:
    """그 거래일 결정들의 `{"<news_id>": [decision_id, …]}`(키는 문자열, 값은 오름차순)."""
    refs: dict[int, set[int]] = {}
    for m, d in days.items():
        for r in conn.execute(
            "SELECT d.id AS did, a.data_json FROM decisions d JOIN analyst_reports a "
            "ON a.decision_date=d.date AND a.market=d.market AND a.ticker=d.ticker "
            "WHERE d.date=? AND d.market=?",
            (d.isoformat(), m),
        ).fetchall():
            for n in news_ids_of(_loads(r["data_json"], {})):
                refs.setdefault(n, set()).add(int(r["did"]))
    return {str(n): sorted(ids) for n, ids in sorted(refs.items())}


def detail(conn: sqlite3.Connection, decision_id: int) -> dict | None:
    """결정 + 라운드(`decision_rounds`) + 요약 문단 + 쓴 뉴스 id. 없으면 None."""
    dec = get_decision(conn, decision_id)
    if dec is None:
        return None
    rounds = [
        _hydrate(r) for r in conn.execute(
            "SELECT * FROM decision_rounds WHERE decision_id=? ORDER BY round", (decision_id,)
        )
    ]  # fmt: skip
    return {
        "decision": dec, "rounds": rounds,
        "summary": approvals.summary_of(conn, {"decision_id": decision_id}),
        "news_refs": news_refs(conn, dec),
    }  # fmt: skip


def outcomes(conn: sqlite3.Connection, decision_id: int) -> list[dict]:
    """계좌별 판정 + (있으면) 그 계좌의 마지막 주문 상태."""
    out = []
    for r in conn.execute(
        "SELECT account_id, approved, adjusted_qty, reasons_json, created_at FROM risk_checks "
        "WHERE decision_id=? ORDER BY id",
        (decision_id,),
    ).fetchall():
        o = conn.execute(
            "SELECT status, order_no, qty, price, mode FROM orders WHERE decision_id=? "
            "AND account_id=? ORDER BY id DESC LIMIT 1",
            (decision_id, r["account_id"]),
        ).fetchone()
        out.append({
            "account_id": r["account_id"], "approved": bool(r["approved"]),
            "adjusted_qty": r["adjusted_qty"], "reasons": _loads(r["reasons_json"], []),
            "checked_at": r["created_at"],
            "order_status": o["status"] if o else None, "order_no": o["order_no"] if o else None,
            "order_qty": o["qty"] if o else None, "order_price": o["price"] if o else None,
            "order_mode": o["mode"] if o else None,
        })  # fmt: skip
    return out


def _gate_or_order(v: dict) -> dict:
    if "approved" in v:
        return {
            "approved": bool(v.get("approved")), "qty": v.get("adjusted_qty"),
            "reasons": v.get("reasons") or [], "note": v.get("note"), "mode": v.get("mode"),
            "dry_run": v.get("dry_run"),
        }  # fmt: skip
    res, req = v.get("result") or {}, v.get("request") or {}
    return {
        "status": v.get("status"), "order_no": res.get("order_no"), "message": res.get("message"),
        "side": req.get("side"), "qty": req.get("qty"), "price": req.get("price"),
        "mode": v.get("mode"),
    }  # fmt: skip


def trace(conn: sqlite3.Connection, decision_id: int, state: Path | None) -> dict | None:
    """결정 하나의 판단 과정: 애널리스트(역할·신호·확신·근거 2개) → 토론(라운드별) → 수렴 요약 →
    결정·라운드 → 게이트(`risk_checks`) → 주문(`orders`) + 재평가·손절·재호가 단계 + 체크포인트
    목록. 없으면 None."""
    dec = get_decision(conn, decision_id)
    if dec is None:
        return None
    rd = _run_path(state, dec["date"], dec["market"], dec["ticker"])
    names = _stages(rd)
    analysts = []
    for r in _reports(conn, dec):
        a = _loads(r["data_json"], {})
        analysts.append({
            "role": r["role"], "label": ROLE_LABEL.get(r["role"], r["role"]),
            "signal": r["signal"], "confidence": r["confidence"], "summary": a.get("summary"),
            "evidence": [e.get("claim") for e in (a.get("evidence") or [])[:2]
                         if isinstance(e, dict)],
            "news_refs": news_ids_of(a),
        })  # fmt: skip
    debate = [
        {"round": r["round"], "side": r["side"], "stance": r["stance"],
         "conviction": r["conviction"], "timing": _loads(r["data_json"], {}).get("timing")}
        for r in conn.execute(
            "SELECT * FROM debate_turns WHERE decision_date=? AND market=? AND ticker=? "
            "ORDER BY round, side",
            (dec["date"], dec["market"], dec["ticker"]),
        )
    ]  # fmt: skip
    rs = _data(rd, "25_research_summary")
    gate = [
        {"account_id": r["account_id"], "approved": bool(r["approved"]),
         "qty": r["adjusted_qty"], "reasons": _loads(r["reasons_json"], []),
         "checked_at": r["created_at"]}
        for r in conn.execute(
            "SELECT * FROM risk_checks WHERE decision_id=? ORDER BY id", (decision_id,)
        )
    ]  # fmt: skip
    orders = [
        {"id": r["id"], "account_id": r["account_id"], "status": r["status"],
         "order_no": r["order_no"], "side": r["side"], "qty": r["qty"], "price": r["price"],
         "mode": r["mode"], "origin": r["origin"], "message": r["message"],
         "at": r["created_at"]}
        for r in conn.execute("SELECT * FROM orders WHERE decision_id=? ORDER BY id",
                              (decision_id,))
    ]  # fmt: skip

    def by_account(prefix: str) -> list[dict]:
        return [
            {"stage": n, "account_id": n[len(prefix) + 1 :], **_gate_or_order(_data(rd, n))}
            for n in names if n.startswith(prefix + "_")
        ]  # fmt: skip

    rounds = [
        {"round": r["round"], "action": r["action"], "stop_loss": r["stop_loss"],
         "source": r["source"], "created_at": r["created_at"],
         "gate": by_account(f"41_risk_check_r{r['round']}"),
         "orders": by_account(f"51_order_r{r['round']}")}
        for r in conn.execute(
            "SELECT * FROM decision_rounds WHERE decision_id=? AND round > 0 ORDER BY round",
            (decision_id,),
        )
    ]  # fmt: skip
    quote = ((_data(rd, "00_inputs").get("technical") or {}).get("quote") or {}).get("price")
    return {
        "decision": dec, "quote": quote, "analysts": analysts, "debate": debate,
        "research": {k: rs.get(k) for k in RESEARCH_KEYS} if rs else None,
        "gate": gate, "orders": orders, "reevaluations": rounds,
        "stop_loss": {"gate": by_account("45_stop_loss"), "orders": by_account("55_order_sl")},
        "reprice": {"gate": by_account("42_reprice"), "orders": by_account("52_order_reprice")},
        "stages": names,
    }  # fmt: skip


def checkpoint(dec: dict, stage: str, state: Path | None) -> dict | None:
    """결정 종목 run 디렉터리의 체크포인트 하나. 단계 이름 형식 오류는 ValueError, 없으면 None.
    디렉터리를 만들지 않는다."""
    validate_ids(dec["ticker"], dec["market"], stage)
    rd = _run_path(state, dec["date"], dec["market"], dec["ticker"])
    cp = run_store.read_checkpoint(rd, stage) if rd.is_dir() else None
    if cp is None:
        return None
    return {"stage": stage, "schema_version": cp.get("schema_version"),
            "data": cp.get("data", cp), "call": cp.get("call")}  # fmt: skip


# --- 시장 today ----------------------------------------------------------------------------


def market_files(mdir: Path) -> list[str]:
    """시장 단위 단계 파일(`_market_*`·`_scan`·`_screen`·`_discovery*` …) 이름(정렬)."""
    if not mdir.is_dir():
        return []
    return sorted(p.stem for p in mdir.glob("_*.json") if p.is_file()
                  and MARKET_STAGE.match(p.stem))  # fmt: skip


def scan_summary(mdir: Path) -> dict | None:
    """`_scan` 의 picks(사유·발굴 촉매)·분석 대상 수·상한 도달·발굴 요약. 없으면 None. 발굴 한 줄
    문구는 scan 기능 몫이라 여기서 만들지 않는다(레인은 scan 을 import 하지 않음)."""
    scan = run_store.read_checkpoint(mdir, "_scan") if mdir.is_dir() else None
    if not scan:
        return None
    disc = scan.get("discovery") or {}
    by_ticker = {c.get("ticker"): c for c in disc.get("candidates") or []}
    picks = []
    for p in scan.get("picks") or []:
        c = by_ticker.get(p.get("ticker"))
        picks.append({"ticker": p.get("ticker"), "reason": p.get("reason"),
                      "discovery": {"tier": c.get("tier"), "catalyst": c.get("catalyst")}
                      if c else None})  # fmt: skip
    return {"picks": picks, "targets": len(scan.get("analyze_targets") or []),
            "cap_hit": bool(scan.get("cap_hit")), "discovery": disc or None}  # fmt: skip


def news_jobs(conn: sqlite3.Connection, market: str, d: date) -> list[dict]:
    """장중 뉴스 발굴이 낸 큐 요청(`job_requests.source='news'`) 중 그 운영일·시장분."""
    lo, hi = xledger.day_bounds(d, market)
    out = []
    for r in conn.execute(
        "SELECT id, kind, params, status, requested_at, finished_at, error FROM job_requests "
        "WHERE source='news' AND requested_at >= ? AND requested_at < ? ORDER BY id DESC LIMIT 200",
        (lo, hi),
    ).fetchall():
        params = _loads(r["params"], {})
        if params.get("market") != market:
            continue
        out.append({"id": r["id"], "kind": r["kind"], "ticker": params.get("ticker"),
                    "status": r["status"], "requested_at": r["requested_at"],
                    "finished_at": r["finished_at"], "error": r["error"]})  # fmt: skip
    return out


def market_today(conn: sqlite3.Connection, market: str, d: date, state: Path | None) -> dict:
    """그 운영일의 종목별 단계 목록(빈 디렉터리 숨김)·종목명·스캔 요약·시장 단계 파일·뉴스 요청."""
    base = state if state is not None else paths.state_dir()
    mdir = run_store.market_dir(d, market, base)
    tickers: dict[str, list[str]] = {}
    if mdir.is_dir():
        for p in sorted(mdir.iterdir()):
            if p.is_dir() and (stages := run_store.list_checkpoints(p)):
                tickers[p.name] = stages
    return {
        "date": d.isoformat(), "market": market, "tickers": tickers,
        "names": {t: names.resolve(conn, market, t) for t in tickers},
        "scan": scan_summary(mdir),
        "market_files": market_files(mdir), "news_jobs": news_jobs(conn, market, d),
    }  # fmt: skip


def market_checkpoint(market: str, d: date, stage: str, state: Path | None) -> dict | None:
    """시장 단위 단계 파일 하나. 이름 형식 오류는 ValueError, 없으면 None."""
    if not MARKET_STAGE.match(stage):
        raise ValueError(f"시장 단계 이름 형식 오류: {stage!r}")
    base = state if state is not None else paths.state_dir()
    mdir = run_store.market_dir(d, market, base)
    cp = run_store.read_checkpoint(mdir, stage) if mdir.is_dir() else None
    if cp is None:
        return None
    if isinstance(cp, dict) and "data" in cp:
        return {"stage": stage, "schema_version": cp.get("schema_version"), "data": cp["data"]}
    return {"stage": stage, "schema_version": None, "data": cp}


# --- risk-checks ---------------------------------------------------------------------------


def risk_checks(
    conn: sqlite3.Connection, days: dict[str, date], account_id: str | None
) -> list[dict]:
    """그 거래일 결정들의 판정(최신 순, 최대 `RISK_CHECKS_LIMIT`). 결정 행이 없는 판정(단계 단위
    실행)은 판정 시각이 KST 그날(KR 거래일)인 것. 날짜 조건은 SQL 에서 먼저 걸고 상한은 그 뒤."""
    if not days:
        return []
    fallback = days.get("KR") or next(iter(days.values()))
    lo, hi = xledger.day_bounds(fallback, "KR")
    by_market = " OR ".join("(d.market=? AND d.date=?)" for _ in days)
    where = f"((d.id IS NOT NULL AND ({by_market})) OR (d.id IS NULL AND rc.created_at >= ? " \
        "AND rc.created_at < ?))"
    args: list[Any] = [x for m, d in days.items() for x in (m, d.isoformat())] + [lo, hi]
    if account_id:
        where += " AND rc.account_id=?"
        args.append(account_id)
    out = []
    for r in conn.execute(
        "SELECT rc.*, d.ticker, d.market, d.date, d.action FROM risk_checks rc "
        f"LEFT JOIN decisions d ON d.id=rc.decision_id WHERE {where} "
        f"ORDER BY rc.id DESC LIMIT {RISK_CHECKS_LIMIT}",
        args,
    ).fetchall():
        row = dict(r)
        row["approved"] = bool(row["approved"])
        row["reasons"] = _loads(row.pop("reasons_json"), [])
        row["name"] = (
            names.resolve(conn, row["market"], row["ticker"])
            if row.get("market") and row.get("ticker") else None
        )
        out.append(row)
    return out


# --- SSE 지문 ------------------------------------------------------------------------------


def _q(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> str:
    row = conn.execute(sql, args).fetchone()
    return ":".join("" if v is None else str(v) for v in tuple(row))


def _status_fp(conn: sqlite3.Connection, table: str) -> str:
    groups = conn.execute(
        f"SELECT status, COUNT(*) FROM {table} GROUP BY status ORDER BY status"
    ).fetchall()
    return _q(conn, f"SELECT COUNT(*), COALESCE(MAX(id), 0) FROM {table}") + ":" + ",".join(
        f"{g[0]}={g[1]}" for g in groups
    )


def decisions_fp(deps: Any) -> str:
    """최근(어제 이후) 결정의 건수·최대 id + 내용 해시(같은 날 재평가는 행을 더하지 않고 json·손절만
    바꾼다) + 라운드 건수·최대 id."""
    since = (deps.now().astimezone(KST).date() - timedelta(days=1)).isoformat()
    with closing(deps.conn_factory()) as conn:
        rows = conn.execute(
            "SELECT id, action, stop_loss, take_profit, data_json FROM decisions WHERE date >= ? "
            "ORDER BY id",
            (since,),
        ).fetchall()
        h = hashlib.sha1(
            "\x1e".join("\x1f".join("" if v is None else str(v) for v in r) for r in rows).encode(
                "utf-8"
            )
        ).hexdigest()[:16]
        return f"{len(rows)}:{rows[-1]['id'] if rows else 0}:{h}|" + _q(
            conn, "SELECT COUNT(*), MAX(id) FROM decision_rounds"
        )


def orders_fp(deps: Any) -> str:
    """주문 건수·최대 id·상태별 건수 + 체결 건수·최대 id(상태 변경·체결 적재도 재조회 대상)."""
    with closing(deps.conn_factory()) as conn:
        return _status_fp(conn, "orders") + "|" + _q(
            conn, "SELECT COUNT(*), COALESCE(MAX(id), 0), SUM(qty) FROM fills"
        )


def triggers_fp(deps: Any) -> str:
    """트리거 건수·최대 id·처리 건수(처리 표시도 재조회 대상)."""
    with closing(deps.conn_factory()) as conn:
        return _q(conn, "SELECT COUNT(*), COALESCE(MAX(id), 0), COUNT(handled_at) FROM triggers")
