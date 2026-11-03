"""execution 코어·레인이 기능에 기대는 접점(Protocol 만). 구현은 해당 서비스 기능이 맡고
조립(boot)이 주입한다."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol


class AccountsPort(Protocol):
    """계좌 목록·브로커·시세·정책 조회."""

    def accounts(self) -> list[Any]: ...

    def brokers(self) -> dict[str, Any]: ...

    def market_client(self) -> Any | None: ...

    def policy(self, conn: Any, account: Any) -> tuple[str, str | None]:
        """계좌의 유효 모드와 사유(effective_mode)."""
        ...

    def paused(self, conn: Any) -> bool: ...

    def holiday_fn(self) -> Callable[[str, date], bool] | None: ...


class InputsPort(Protocol):
    """종목 입력(공시·뉴스·거시 등) 수집."""

    def collect(
        self,
        *,
        ticker: str,
        market: str,
        exchange: str | None,
        asof: datetime | None,
        force: bool,
        conn_factory: Callable[[], Any],
    ) -> dict:
        """`00_inputs` 를 저장하고 그 payload 를 돌려준다."""
        ...


class RegimePort(Protocol):
    """시장 국면(bull·neutral·bear)·헤지·킬스위치·블랙아웃 조회와 관측."""

    def hedge_cfg(self) -> Any:
        """헤지 설정(pydantic 모델). 레인은 속성만 읽는다."""
        ...

    def state(self, conn: sqlite3.Connection, market: str) -> str:
        """`bull|neutral|bear`. 헤지 비활성이거나 플래그가 없으면 `"bull"`."""
        ...

    def load(self, conn: sqlite3.Connection, market: str) -> dict | None:
        """저장된 국면 상태(없으면 None)."""
        ...

    def core_count(self, state: dict | None) -> int:
        """국면 상태가 가리키는 핵심 지표 신호 수."""
        ...

    def view_today(self, conn: sqlite3.Connection, market: str, today: date) -> dict | None:
        """오늘의 시장 판단(없으면 None)."""
        ...

    def clamp_weight(self, view: dict | None) -> float:
        """시장 판단으로 정한 비중 상한(clamp) 값."""
        ...

    def hedge_tickers(self, market: str) -> frozenset[str]:
        """헤지 종목 집합. 헤지 비활성이면 빈 집합."""
        ...

    def instrument(self, market: str, *, leveraged: bool) -> Any | None:
        """헤지 상품(`.symbol`·`.exchange`), 없으면 None."""
        ...

    def killswitch_reason(self, conn: sqlite3.Connection, account_id: str | None) -> str | None:
        """킬스위치가 켜져 있으면 사유, 아니면 None."""
        ...

    def killswitch_observe(
        self,
        conn: sqlite3.Connection,
        *,
        account_id: str,
        market: str,
        nav: Decimal,
        today: date,
        limit_pct: float,
        positions: Any = None,
    ) -> bool:
        """NAV 를 관측해 킬스위치를 판정한다. 새로 켜졌으면 True."""
        ...

    def blackout_reason(self, conn: sqlite3.Connection, ticker: str, today: date) -> str | None:
        """예탁원 권리 일정(유상·무상증자·액면교체·합병분할) 블랙아웃이면 사유, 아니면 None."""
        ...


class ScanPort(Protocol):
    """종목 테마·섹터 조회와 스캔 실행."""

    def theme_of(
        self, conn: sqlite3.Connection, market: str, today: date
    ) -> Callable[[str], list[str] | None]:
        """종목 → 테마 목록(모르면 None) 함수."""
        ...

    def run_scan(
        self, ctx: Any, *, market: str, cancel: Any | None = None, dry_run: bool = False
    ) -> dict:
        """시장 스캔을 실행하고 결과 요약을 돌려준다."""
        ...

    def sector_of(self, market: str) -> Callable[[str], str | None]:
        """종목 → 섹터(모르면 None) 함수."""
        ...


class MlPort(Protocol):
    """정량(ML) 모델 조회·보고·스냅샷 — 구현은 ml 기능이 맡는다."""

    def quant_enabled(self) -> bool: ...

    def latest_model(self, market: str | None) -> tuple[Any, dict] | None:
        """(모델, 메타) 최신본. 없으면 None."""
        ...

    def latest_row(self, ticker: str, market: str, *, asof: date | None) -> dict | None:
        """종목의 피처 행(asof 이하 최신). 없으면 None."""
        ...

    def build_report(self, model: Any, meta: dict, row: dict, *, ticker: str, asof: str) -> dict:
        """`QuantReport` 모양의 dict."""
        ...

    def peer_medians(
        self, *, market: str, ticker: str, asof_date: date, sectors: Any
    ) -> dict | None:
        """동종 섹터 피처 중앙값. 없으면 None."""
        ...

    def snapshot(
        self, markets: list[str], *, tickers: list[tuple[str, str | None]] | None = None
    ) -> int:
        """피처 스냅샷을 적재하고 행 수를 돌려준다."""
        ...
