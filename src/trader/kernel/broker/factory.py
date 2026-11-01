"""브로커 조립: 앱키별 KisClient 프로세스 캐시, 시세·계좌 브로커 생성.

토큰 캐시와 레이트 DB 는 `TRADER_KIS_SHARED_DIR` 가 있으면 거기에 둔다(여러 작업 폴더가 같은
앱키를 쓸 때 한도·토큰을 하나로 공유). 휴장일 캐시는 이 위치와 무관하게 `state_dir()` 에 남는다.
시크릿·설정 의존(키 고르기, 같은 키 경고, 계좌 순회)은 호출하는 기능이 맡는다.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from trader.kernel.broker.auth import KisAuth
from trader.kernel.broker.broker import KisBroker
from trader.kernel.broker.client import RATE_PER_SEC, KisClient
from trader.kernel.broker.creds import KisCreds
from trader.kernel.broker.fo import KisFoBroker
from trader.kernel.broker.market import KisMarket
from trader.kernel.broker.models import BrokerAccount, FoProduct
from trader.kernel.broker.rate_store import SharedRateLimiter
from trader.kernel.platform import paths

logger = logging.getLogger("trader.broker")

SHARED_DIR_ENV = "TRADER_KIS_SHARED_DIR"

_clients: dict[tuple[str, str], KisClient] = {}


def shared_state_dir() -> Path:
    """토큰 캐시·레이트 DB 디렉터리: 환경변수 경로(없으면 생성), 없으면 state_dir().
    상대경로는 작업 폴더마다 다른 곳을 가리켜 공유가 깨지므로 거부한다(`~` 는 펼친다)."""
    raw = os.environ.get(SHARED_DIR_ENV, "").strip()
    if not raw:
        return paths.state_dir()
    d = Path(raw).expanduser()
    if not d.is_absolute():
        raise ValueError(f"{SHARED_DIR_ENV} 는 절대경로여야 합니다: {raw!r}")
    d = d.resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def client_for(creds: KisCreds) -> KisClient:
    """앱키별 KisClient. 같은 프로세스에서는 (env, 앱키)마다 하나만 만들어 레이트리미터·토큰을
    공유한다 — KIS 초당 한도·토큰은 앱키 단위다. 생성 시 네트워크를 부르지 않는다."""
    if not creds.app_key or not creds.app_secret:
        raise ValueError(f"KIS 앱키가 없습니다: {creds.prefix}")
    cache_key = (creds.env, creds.app_key)
    if cache_key not in _clients:
        base = shared_state_dir()
        shared = bool(os.environ.get(SHARED_DIR_ENV, "").strip())
        logger.info("KIS 토큰·레이트 디렉터리: %s (%s)", base, "공유" if shared else "state")
        # 데몬·세션 도구 서브프로세스가 같은 1초 창을 나눠 쓴다(교차 프로세스 한도, 앱키별)
        limiter = SharedRateLimiter(RATE_PER_SEC[creds.env], base / "kis_rate.db", creds.bucket)
        auth = KisAuth(creds.env, creds.app_key, creds.app_secret, state=base, name=creds.bucket)
        _clients[cache_key] = KisClient(creds.env, auth, limiter=limiter)
    return _clients[cache_key]


def reset_clients() -> None:
    """프로세스 캐시를 비운다(테스트용). 열린 http 클라이언트는 닫지 않는다."""
    _clients.clear()


def build_market(creds: KisCreds) -> KisMarket:
    """시세 클라이언트(실전 env 전용 — KisMarket 이 검사한다). 휴장일 캐시는 state_dir()."""
    return KisMarket(client_for(creds))


def build_broker(
    account: BrokerAccount, creds: KisCreds, *, fo_product: FoProduct | None = None
) -> KisBroker | KisFoBroker:
    """계좌 한 개의 브로커. 파생(`asset == "fo"`) 계좌는 상품(`fo_product`)이 필수다."""
    client = client_for(creds)
    if account.asset == "fo":
        if fo_product is None:
            raise ValueError(f"파생 계좌에는 fo_product 가 필요합니다: {account.id}")
        return KisFoBroker(account, client, product=fo_product)
    return KisBroker(account, client)
