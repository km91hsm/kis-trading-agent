"""execution 코어·레인이 기능에 기대는 접점(Protocol 만). 구현은 해당 서비스 기능이 맡고
조립(boot)이 주입한다."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol


class AccountsPort(Protocol):
    """계좌 목록·브로커·시세·정책 조회."""

    def accounts(self) -> list[Any]: ...

    def brokers(self) -> list[Any]:
        """현물 브로커 목록(각 `.account.id` 로 계좌를 안다). 키 없는 계좌는 빠진다."""
        ...

    def market_client(self) -> Any | None: ...

    def policy(self, conn: Any, account: Any) -> tuple[str, str | None]:
        """계좌의 유효 모드와 사유(effective_mode)."""
        ...

    def paused(self, conn: Any) -> bool: ...

    def set_paused(
        self, conn: Any, value: bool, *, channel: str = "cli", actor: str | None = None
    ) -> None:
        """원장 `paused` 플래그를 쓰고 바뀌었으면 control_log 에 남긴다(트레이 등 커널 진입점용)."""
        ...

    def holiday_fn(self) -> Callable[[str, date], bool] | None: ...

    def fo_accounts(self) -> list[Any]:
        """파생(asset fo) 계좌와 그 브로커 — 레인 fo 전용. 현물 접근자는 fo 를 제외한다."""
        ...

    def fo_brokers(self) -> dict[str, Any]:
        """파생(asset fo) 계좌와 그 브로커 — 레인 fo 전용. 현물 접근자는 fo 를 제외한다."""
        ...


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

    def research_context(
        self,
        conn: Any,
        market: str,
        *,
        asof: datetime,
        market_client: Any = None,
        http: Any = None,
        now: Callable[[], datetime] | None = None,
    ) -> dict:
        """계좌·주문 없이 거시·공개 블로그·KIS 활동의 원천/시각/품질 입력을 수집한다."""
        ...

    def recent(
        self,
        conn: Any,
        market: str,
        *,
        tickers: Iterable[str] | None,
        since: datetime,
        limit: int = 20,
        novel_only: bool = False,
        asof: datetime | None = None,
        untagged_only: bool = False,
    ) -> list[dict]:
        """`since` 이후·`asof`(없으면 지금) 이전에 수신한 시장 뉴스(최신순, 최대 `limit`). 행은
        `id·title·published_at·received_at·tickers(list[str])·novel(bool)·source` 를 담는다.
        `tickers` 를 주면 그중 하나라도 태그된 뉴스만, `novel_only` 면 유사 중복이 아닌 것만,
        `untagged_only` 면 종목 태그 없는 시장·거시 뉴스만."""
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

    def watchlist(self, conn: sqlite3.Connection, market: str | None) -> list[str]:
        """런타임 관심종목. market 을 주면 그 시장 종목만 접두사 없이."""
        ...

    def classify(
        self,
        conn: sqlite3.Connection,
        market: str,
        tickers: Iterable[str],
        *,
        exchanges: Mapping[str, str | None] | None = None,
    ) -> None:
        """sectors.yaml·자동 캐시에 없는 종목을 KIS 업종으로 대분류해 자동 캐시에 적는다
        (`sector_of` 가 읽는다). `exchanges` 는 US 종목 → 거래소(NAS|NYS|AMS). 조회 실패는 같은
        날 다시 묻지 않는다. 시세 클라이언트가 없으면 아무것도 하지 않는다."""
        ...


class StockExecPort(Protocol):
    """현물 집행 서비스(spot 제공). 결정 행 없는 파이썬 주문(단타)을 게이트·승인·멱등·기록을
    거쳐 낸다."""

    def execute(
        self,
        conn: sqlite3.Connection,
        *,
        decision: dict,
        date: date,
        market: str,
        price: Decimal,
        exchange: str | None,
        dry_run: bool,
        scalp_leg: str,
        skip_accounts: frozenset[str],
        max_qty: dict[str, int] | None,
        trade_block: str | None,
        notifier: Any,
        now: Callable[[], datetime],
        state: Path | None,
    ) -> list[dict]:
        """`decision`(Decision 모양 dict)을 계좌마다 판정·주문하고 계좌별 결과를 돌려준다."""
        ...

    def cancel(
        self,
        conn: sqlite3.Connection,
        *,
        account_id: str,
        order_id: int,
        order_no: str,
        market: str,
        exchange: str | None,
        dry_run: bool,
        reason: str,
        notifier: Any,
        now: Callable[[], datetime],
    ) -> dict:
        """미체결 주문 1건을 코어 취소 경로로 취소한다."""
        ...

    def sync_fills(
        self,
        conn: sqlite3.Connection,
        *,
        account_id: str,
        d: date,
        now: Callable[[], datetime],
        notifier: Any,
    ) -> dict:
        """계좌 1개의 체결을 원장에 적재한다. 계좌·브로커가 없으면 `{"error": …}`."""
        ...


class ScalpPort(Protocol):
    """단타 레인 조회(scalp 제공) — 다른 레인이 단타 보유분을 건드리지 않게 한다."""

    def open_tickers(self, state: Path | None, market: str, today: date) -> set[str]:
        """오늘 단타가 들고 있는(`open` — 청산 대기 포함, 판정 대기 `pending` 제외) 종목 집합."""
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
        self,
        markets: list[str],
        *,
        tickers: list[tuple[str, str | None]] | None = None,
        extra_targets: list[tuple[str, str, str | None]] | None = None,
    ) -> int:
        """피처 스냅샷을 적재하고 행 수를 돌려준다. `extra_targets` 는 대상에 더한다."""
        ...

    def close_table(
        self, market: str, since: date, until: date
    ) -> Mapping[str, Mapping[date, float | None]]:
        """종목 → 날짜 → 종가. 결측은 None, 자료가 없으면 빈 매핑."""
        ...


class EvaluationPort(Protocol):
    """판단 기억·보정·서킷브레이커·RL 사이저 — 구현은 evaluation 기능이 맡는다."""

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
        """유사 과거 판단 기억 블록."""
        ...

    def calibration_injected(self) -> bool:
        """보정 리포트를 판단 입력에 주입하는지."""
        ...

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
        """종목의 보정 리포트. 없으면 None."""
        ...

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
        """판단을 메모리 엔트리로 기록하고 그 엔트리 id 를 돌려준다(기록하지 않으면 None).
        섀도 장부는 건드리지 않는다."""
        ...

    def update_breaker(
        self, conn: sqlite3.Connection, *, market: str, nav_series: list[tuple[str, Decimal]]
    ) -> dict | None:
        """NAV 계열로 서킷브레이커 상태를 갱신한다."""
        ...

    def rl_sizer(self, state: Path | None) -> Any | None:
        """RL 사이저(승격본). 없으면 None."""
        ...

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
        """(관측 벡터, 제안 비중, 기준 벡터)."""
        ...

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
        """RL 제안을 기록하고 행 id 를 돌려준다."""
        ...
