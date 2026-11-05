"""ml 데몬 잡: `ml_train`(주간 퀀트 재학습)·`universe_members`(시점 유니버스 기록)·
`features_snapshot`(일일 피처 스토어 스냅샷)와 큐 kind
`features_backfill|snapshot|derive`·`ml_train`, 기동 훅 `daemon.bootstrap`(피처 스토어가 비면
백필+학습 요청).

옛 데몬 `_ml_train_body`(RL 사이저 학습은 M8 로 이연)·`_universe_members_body`·`_features_action`·
`_daemon_features_fn`·`_bootstrap_queue` 의 피처 부트스트랩 꼬리.
시세·계좌는 `deps.ports["accounts"]`."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from trader.features.ingest import api as ingest_api
from trader.features.ml import features, universe_pit
from trader.features.ml.messages import ml_trained
from trader.features.ml.settings import load_ml
from trader.features.ml.train import train_quant
from trader.features.regime import api as regime_api
from trader.kernel.config import Market
from trader.kernel.daemon import queue as jobq
from trader.kernel.daemon.queue_jobs import enqueue_from_job
from trader.kernel.execution import ledger as xledger
from trader.kernel.notify import messages as kmessages
from trader.kernel.notify.telegram import NullNotifier
from trader.kernel.registry.types import JobSpec, QueueKind

logger = logging.getLogger("trader.ml")

BOOTSTRAP_RETRY_AGE = timedelta(hours=24)  # 백필이 이 안에 실패했으면 기동 때 다시 넣지 않는다


# --- 파라미터 ------------------------------------------------------------------------------


class FeaturesBackfillParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market: Market | None = None
    years: int = Field(3, ge=1, le=4)


class FeaturesSnapshotParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market: Market | None = None


class EmptyParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MlTrainParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market: Market | None = None


# --- 잡 본문 -------------------------------------------------------------------------------


def _accounts_port(deps):
    return (deps.ports or {}).get("accounts")


def ml_train_body(deps, job=None, *, market: str | None = None) -> dict:
    notifier = deps.notifier or NullNotifier()
    try:
        r = train_quant(load_ml(deps.registry), state=deps.state, now=deps.now, market=market)
    except Exception as e:  # noqa: BLE001 - 학습 실패는 통보하고 데몬은 계속
        logger.exception("ML 학습 실패")
        notifier.send(kmessages.failure("ML 학습 실패", "", e))
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}
    met = r.get("metrics") or {}
    acc = (
        f"정확도 {met['accuracy']:.3f} / 기준선 {met['baseline']:.3f}"
        if met
        else r.get("promote_reason")
    )
    notifier.send(
        ml_trained(
            kind="퀀트",
            version=r.get("version"),
            trained=r.get("status") == "trained",
            detail=acc,
            promoted=bool(r.get("promoted")) if r.get("status") == "trained" else None,
        )
    )
    return r


def ml_train_request(deps, params: dict, progress) -> dict:
    """큐 `ml_train` 본문: 전 시장 또는 지정 시장."""
    return ml_train_body(deps, market=params.get("market"))


def universe_members_body(deps, job) -> dict:
    """그날의 유니버스(제외 목록 뺌) + 계좌 보유 종목을 시장별 운영일로 기록한다(시점 기준 학습)."""
    uni = ingest_api.load_universe()
    port = _accounts_port(deps)
    accounts = port.accounts() if port is not None else []
    out: dict[str, int] = {}
    conn = deps.conn_factory()
    try:
        for m in deps.settings.markets:
            if m == "KR":
                tickers = {t for t in uni.kr if t not in uni.exclude}
            else:
                tickers = {s.symbol for s in uni.us if s.symbol not in uni.exclude}
            for a in accounts:
                if m in a.markets:
                    snap = xledger.latest_positions(conn, a.id, m) or {}
                    tickers |= {str(p["ticker"]) for p in snap.get("positions") or []}
            tickers -= regime_api.excluded_tickers(deps.settings, m)  # 헤지·현금 운용 ETF 제외
            out[m] = universe_pit.record(
                conn, market=m, tickers=tickers, day=universe_pit.effective_day(m, deps.now())
            )
    finally:
        conn.close()
    return out


def features_action(deps, action: str, p: dict, progress) -> dict:
    """큐 `features_*` 본문. derive 는 KIS 호출이 없어 시세 클라이언트 없이도 돈다."""
    if action == "derive":
        return {"rows": int(features.rederive(progress=progress))}
    port = _accounts_port(deps)
    market_client = port.market_client() if port is not None else None
    if market_client is None:
        return {"error": "시세 클라이언트 없음(피처 작업 불가)"}
    markets = [p["market"]] if p.get("market") else list(deps.settings.markets)
    fn = features.backfill if action == "backfill" else features.snapshot
    kw = {"years": p["years"]} if action == "backfill" else {}
    n = fn(
        market_client=market_client,
        dart=ingest_api.build_dart(deps.secrets),
        edgar=ingest_api.build_edgar(ingest_api.load_sources(registry=deps.registry), deps.secrets),
        universe=ingest_api.load_universe(),
        markets=markets,  # type: ignore[arg-type]
        progress=progress,
        **kw,
    )
    return {"rows": int(n)}


def features_snapshot_body(deps, job=None) -> dict:
    """일일 피처 스토어 스냅샷(전 시장). 큐 kind 와 같은 `features_action`."""
    return features_action(deps, "snapshot", {}, lambda *_a, **_k: None)


def features_request_for(action: str):
    def body(deps, params: dict, progress) -> dict:
        return features_action(deps, action, params, progress)

    return body


def bootstrap(*, deps, **_) -> dict:
    """기동 훅: 시세 클라이언트가 있는데 피처 스토어가 비면 백필+학습을 큐에 넣는다. 백필이
    24시간 안에 실패했으면 다시 넣지 않는다(재기동 루프 방지). 어떤 오류도 올리지 않는다."""
    queued: list[str] = []
    try:
        port = _accounts_port(deps)
        if port is None or port.market_client() is None or features.has_files(deps.state):
            return {"queued": queued}
        conn = deps.conn_factory()
        try:
            last = jobq.latest_by(conn, "features_backfill", source="daemon")
        finally:
            conn.close()
        recent_fail = bool(
            last
            and last["status"] == "error"
            and last.get("finished_at")
            and datetime.fromisoformat(last["finished_at"]) > deps.now() - BOOTSTRAP_RETRY_AGE
        )
        if not recent_fail:
            for kind, params in (("features_backfill", {"years": 3}), ("ml_train", {})):
                if enqueue_from_job(deps, kind, params, source="daemon") is not None:
                    queued.append(kind)
    except Exception as e:  # noqa: BLE001 - 부트스트랩 실패가 기동을 막지 않게
        logger.warning("피처 부트스트랩 실패: %s", e)
        return {"queued": queued, "error": f"{type(e).__name__}: {e}"}
    return {"queued": queued}


# --- 선언 ----------------------------------------------------------------------------------

ML_TRAIN = JobSpec(
    id="ml_train", kind="ml_train", trigger="cron", per_market=False, schedule_key="ml_train",
    interval_s=None, enabled=None, gate="none", heavy=True, misfire="idempotent",
    catch_up="none", start_soon_s=0, body=ml_train_body,
)  # fmt: skip

UNIVERSE_MEMBERS = JobSpec(
    id="universe_members", kind="universe_members", trigger="cron", per_market=False,
    schedule_key="universe_members", interval_s=None, enabled=None, gate="none", heavy=False,
    misfire="idempotent", catch_up="direct", start_soon_s=0, body=universe_members_body,
)  # fmt: skip

FEATURES_SNAPSHOT = JobSpec(
    id="features_snapshot", kind="features_snapshot", trigger="cron", per_market=False,
    schedule_key="features_snapshot", interval_s=None, enabled=None, gate="none", heavy=True,
    misfire="idempotent", catch_up="direct", start_soon_s=0, body=features_snapshot_body,
)  # fmt: skip

QUEUE_KINDS = (
    QueueKind(
        "features_backfill", FeaturesBackfillParams, "피처 백필", "slow", 0, False, False,
        features_request_for("backfill"),
    ),
    QueueKind(
        "features_snapshot", FeaturesSnapshotParams, "피처 스냅샷", "slow", 0, False, False,
        features_request_for("snapshot"),
    ),
    QueueKind(
        "features_derive", EmptyParams, "파생 열 재계산", "slow", 0, False, False,
        features_request_for("derive"),
    ),
    QueueKind(
        "ml_train", MlTrainParams, "퀀트 학습", "slow", 0, False, False, ml_train_request
    ),
)
