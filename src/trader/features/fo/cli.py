"""`trader fo …` 명령: 종목 마스터 조회·옵션 체인·브로커 계약 검증·세션·모니터·승계·청산.

조회 전용 명령(`contracts`·`chain`·`verify`)은 어떤 주문도 내지 않는다. `verify` 는 보유 행이
실제로 있어 방향·종류·NAV 가 확인된 수단만 control `fo_verified:<env>:<계좌>:<상품>:<수단>` 에
`CONTRACT_VERSION` 을 적는다(빈 응답은 검증이 아니다). `order-test`(모의 전용)·`monitor`·`close`
는 주문이 나가므로 사용자만 실행한다.

계좌·브로커·시세는 `accounts` 포트(`boot(ports=True)`)에서 받는다 — 레인은 다른 기능을 import 하지
않는다. 마스터는 `state/fo` 캐시(24h, 지나면 다운로드). 실행 컨텍스트는 데몬 잡과 같은
`jobs._fo_ctx` 로 만든다."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import typer

from trader.features.fo import engine, executor, jobs, positions, store
from trader.features.fo import order_test as fo_order_test
from trader.features.fo import session as fo_session
from trader.features.fo import status as fo_status
from trader.kernel.broker import fo_master
from trader.kernel.broker.fo import CONTRACT_VERSION
from trader.kernel.broker.models import FoBalance, FoPosition
from trader.kernel.cli.boot import Boot, boot, config_errors
from trader.kernel.config import KST, ConfigError
from trader.kernel.config.market_hours import is_fo_open
from trader.kernel.ledger import control
from trader.kernel.notify.telegram import make_notifier
from trader.kernel.platform import paths

fo_app = typer.Typer(
    help="선물옵션 레인: 마스터·체인·브로커 계약 검증", no_args_is_help=True
)

VEHICLES = ("FUTURES", "OPTION_BUY", "OPTION_SELL")


def _master() -> list[fo_master.Contract]:
    return fo_master.load(cache_dir=paths.state_dir() / "fo")


def _check_product(settings: Any, product: str | None) -> tuple[str, Any]:
    name = product or settings.fo.product
    if name not in settings.fo.products:
        raise ConfigError(f"fo.products 에 없는 상품: {name}")
    return name, settings.fo.products[name]


def _accounts_port(b: Boot) -> Any:
    port = b.ports.get("accounts")
    if port is None:
        raise ConfigError("계좌 포트가 없습니다(accounts 기능 확인)")
    return port


def _market_client(b: Boot) -> Any:
    """시세 클라이언트(계좌 포트). 시세 키가 없으면 ConfigError."""
    mc = _accounts_port(b).market_client()
    if mc is None:
        raise ConfigError("시세 클라이언트를 만들 수 없어요 (계좌·시세 키 확인)")
    return mc


def _deps(b: Boot, *, dry_run: bool) -> SimpleNamespace:
    """`jobs._fo_ctx` 가 읽는 데몬 deps 모양(CLI 용)."""
    return SimpleNamespace(
        ports=b.ports, settings=b.settings, notifier=make_notifier(b.secrets),
        state=paths.state_dir(), now=lambda: datetime.now(UTC), dry_run=dry_run,
        secrets=b.secrets,
    )  # fmt: skip


def _exec_ctx(b: Boot, *, dry_run: bool):
    """파생 실행 컨텍스트(CLI 용) — 데몬 잡과 같은 조립(`jobs._fo_ctx`). 마스터 실패는 경고 뒤 빈
    목록, 시세 클라이언트가 없으면 `market_client=None`."""
    return jobs._fo_ctx(_deps(b, dry_run=dry_run), b.conn)


def _today(value: str | None) -> date:
    return date.fromisoformat(value) if value else datetime.now(KST).date()


def _no_quote(_: fo_master.Contract) -> date | None:
    return None  # 시세 없이는 둘째 목요일 규칙(휴일 앞당김 미반영)


def _front_futures(
    contracts: list[fo_master.Contract],
    product: Any,
    *,
    today: date,
    roll_days: int,
) -> fo_master.Contract | None:
    return fo_master.front_month(
        contracts,
        product,
        "FUTURES",
        today=today,
        roll_days=roll_days,
        last_trade_date_of=_no_quote,
    )


def _option_front(
    contracts: list[fo_master.Contract],
    product: Any,
    *,
    today: date,
    roll_days: int,
) -> tuple[str | None, list[fo_master.Contract]]:
    return fo_master.option_front(
        contracts, product, today=today, roll_days=roll_days, last_trade_date_of=_no_quote
    )


def _vehicle_of(p: FoPosition) -> str:
    if p.kind == "FUTURES":
        return "FUTURES"
    return "OPTION_BUY" if p.contract_side == "LONG" else "OPTION_SELL"


def verified_vehicles(balance: FoBalance) -> list[str]:
    """잔고에서 방향·종류가 확정된 보유 행(`FoPosition`)이 있고 NAV 가 있을 때, 그 행이 속한 수단.
    `UnverifiedHolding` 과 빈 잔고는 아무것도 검증하지 않는다."""
    if balance.cash.nav is None:
        return []
    seen = {_vehicle_of(p) for p in balance.positions}
    return [v for v in VEHICLES if v in seen]


@fo_app.command("contracts")
@config_errors
def contracts_cmd(
    product: str | None = typer.Option(
        None, "--product", help="mini|regular (기본 settings.fo.product)"
    ),
    today: str | None = typer.Option(None, "--today", help="기준일 YYYY-MM-DD (기본 오늘 KST)"),
) -> None:
    """종목 마스터(24h 캐시)에서 선물 월물·옵션 행사가 표를 JSON 으로. 주문·기록 없음."""
    settings = boot(settings=True).settings
    name, pcfg = _check_product(settings, product)
    d = _today(today)
    cs = _master()
    futs = fo_master.of_product(cs, pcfg, "FUTURES")
    front = _front_futures(cs, pcfg, today=d, roll_days=settings.fo.roll_days)
    ym, chain = _option_front(cs, pcfg, today=d, roll_days=settings.fo.roll_days)
    months = sorted({c.expiry_ym for c in futs if c.expiry_ym})
    out = {
        "product": name,
        "today": d.isoformat(),
        "contracts": len(cs),
        "futures": {
            "front": _contract_json(front) if front else None,
            "months": [_contract_json(next(c for c in futs if c.expiry_ym == m)) for m in months],
        },
        "options": {
            "front": {
                "expiry_ym": ym,
                "last_trade_date": fo_master.second_thursday(ym).isoformat() if ym else None,
                "strikes": sorted(
                    {str(c.strike) for c in chain if c.strike is not None}, key=Decimal
                ),
                "calls": sum(1 for c in chain if c.kind == "CALL"),
                "puts": sum(1 for c in chain if c.kind == "PUT"),
            }
            if ym
            else None
        },
    }
    typer.echo(json.dumps(out, ensure_ascii=False))


def _contract_json(c: fo_master.Contract) -> dict[str, Any]:
    return {
        "code": c.code,
        "name": c.name,
        "kind": c.kind,
        "expiry_ym": c.expiry_ym,
        "last_trade_date": fo_master.second_thursday(c.expiry_ym).isoformat()
        if c.expiry_ym
        else None,
        "strike": str(c.strike) if c.strike is not None else None,
    }


@fo_app.command("chain")
@config_errors
def chain_cmd(
    product: str | None = typer.Option(None, "--product", help="mini|regular"),
    strikes: int = typer.Option(4, "--strikes", help="ATM 양쪽으로 몇 단계"),
    today: str | None = typer.Option(None, "--today", help="기준일 YYYY-MM-DD"),
) -> None:
    """가까운 월물 옵션 체인(ATM ± n 행사가)의 시세·그릭을 JSON 으로(실전 시세 클라이언트,
    조회만)."""
    b = boot(ports=True)
    try:
        name, pcfg = _check_product(b.settings, product)
        d = _today(today)
        cs = _master()
        ym, chain = _option_front(cs, pcfg, today=d, roll_days=b.settings.fo.roll_days)
        if not ym:
            raise ConfigError("roll 창 밖의 옵션 월물이 없습니다")
        market = _market_client(b)
    finally:
        b.conn.close()
    underlying, asof = market.get_kospi200()
    ks = sorted({c.strike for c in chain if c.strike is not None})
    if not ks:
        raise ConfigError(f"옵션 월물 {ym} 에 행사가가 있는 계약이 없습니다")
    atm = min(range(len(ks)), key=lambda i: abs(ks[i] - underlying))
    wanted = ks[max(0, atm - strikes) : atm + strikes + 1]
    quotes, errors = market.get_option_chain(chain, strikes=wanted)
    out = {
        "product": name,
        "expiry_ym": ym,
        "underlying": str(underlying),
        "underlying_asof": asof.isoformat() if asof else None,
        "strikes": [str(k) for k in wanted],
        "quotes": [q.model_dump(mode="json") for q in quotes],
        "errors": errors,
    }
    typer.echo(json.dumps(out, ensure_ascii=False))


def _find_fo_account(b: Boot, account: str) -> Any:
    """파생 계좌(`accounts` 포트의 fo_accounts). 현물 계좌 id 면 asset 오류로 알려 준다."""
    port = _accounts_port(b)
    acct = next((a for a in port.fo_accounts() if a.id == account), None)
    if acct is not None:
        return acct
    if any(a.id == account for a in port.accounts()):
        raise ConfigError(f"계좌 {account} 은 asset fo 가 아닙니다(현물 계좌)")
    raise ConfigError(f"계좌 id 를 찾을 수 없습니다: {account}")


def _fo_broker(b: Boot, acct: Any) -> Any:
    broker = _accounts_port(b).fo_brokers().get(acct.id)
    if broker is None:
        raise ConfigError("앱키가 없어 파생 브로커를 만들지 못했습니다")
    return broker


def _mask_cash(cash) -> dict[str, bool]:
    """금액은 값이 아니라 '있음/없음' 만 남긴다(비식별 스냅샷). 모델이 강제하는 필드는 늘 있으므로
    선택 필드 NAV 만 보고한다."""
    return {"nav": cash.nav is not None}


def _k200_json(r: tuple[Decimal, datetime | None]) -> dict[str, Any]:
    px, asof = r
    return {"price": str(px), "asof": asof.isoformat() if asof else None}


def _quote_json(q) -> dict[str, Any]:
    return {
        "code": q.code,
        "kind": q.kind,
        "price": str(q.price),
        "bid": q.bid is not None,
        "ask": q.ask is not None,
        "basis": q.basis is not None,
        "last_trade_date": q.last_trade_date.isoformat() if q.last_trade_date else None,
        "days_left": q.days_left,
        "underlying": q.underlying is not None,
        "quote_asof": q.quote_asof.isoformat() if q.quote_asof else None,
    }


def _orderable_json(o) -> dict[str, Any]:
    return {
        "tot_qty": o.tot_qty,
        "lqd_qty": o.lqd_qty,
        "ord_qty": o.ord_qty,
        "cash": o.cash is not None,
        "bass_idx": o.bass_idx is not None,
    }


def _balance_json(b: FoBalance) -> dict[str, Any]:
    return {
        "positions": len(b.positions),
        "unverified": len(b.unverified),
        "rows": [
            {"code": p.code, "kind": p.kind, "side": p.contract_side, "qty": p.qty}
            for p in b.positions
        ],
        "unverified_reasons": [u.reason for u in b.unverified],
        **_mask_cash(b.cash),
    }


@fo_app.command("verify")
@config_errors
def verify_cmd(
    account: str = typer.Option(..., "--account", help="선물옵션 계좌 id (asset: fo)"),
    vehicle: str | None = typer.Option(
        None, "--vehicle", help="FUTURES|OPTION_BUY|OPTION_SELL 하나만 검증(기본 전부)"
    ),
) -> None:
    """조회 전용 계약 검증: 마스터·코스피200·선물 시세·주문가능·잔고를 읽고 비식별 스냅샷 JSON 을
    출력한다. 모든 단계가 성공했고 보유 행으로 방향·종류·NAV 가 확인된 수단만 `fo_verified:*`
    플래그를 적는다(한 단계라도 실패하면 플래그 없이 exit 1)."""
    if vehicle is not None and vehicle not in VEHICLES:
        raise ConfigError(f"--vehicle 은 {'|'.join(VEHICLES)} 중 하나")
    b = boot(ports=True, log_name="fo")
    settings, conn = b.settings, b.conn
    try:
        acct = _find_fo_account(b, account)
        errors: list[str] = []
        out: dict[str, Any] = {
            "account": acct.id,
            "env": acct.env,
            "product": settings.fo.product,
            "contract_version": CONTRACT_VERSION,
        }

        def step(name: str, fn: Callable[[], Any]) -> Any:
            try:
                r = fn()
            except Exception as e:  # noqa: BLE001 — 점검은 모든 실패를 보고한다
                errors.append(f"{name}: {type(e).__name__}: {e}")
                out[name] = None
                return None
            out[name] = r
            return r

        pcfg = settings.fo.product_cfg()
        d = datetime.now(KST).date()
        cs = step("master", _master) or []
        out["master"] = {"contracts": len(cs)} if cs else out["master"]
        if not cs and not errors:
            errors.append("master: 계약이 없습니다")
        front = _front_futures(cs, pcfg, today=d, roll_days=settings.fo.roll_days) if cs else None
        out["front"] = _contract_json(front) if front else None
        if cs and front is None:
            # 월물이 없으면 시세·주문가능 단계를 못 돌리므로 검증 실패로 센다(플래그 없음)
            errors.append("front: roll 창 밖 선물 월물이 없습니다")

        market = step("market_client", lambda: _market_client(b))
        out["market_client"] = market is not None
        if market is not None:
            step("kospi200", lambda: _k200_json(market.get_kospi200()))
            if front is not None:
                quote = step(
                    "quote", lambda: _quote_json(market.get_fo_price(front.code, "FUTURES"))
                )
            else:
                quote = None
        else:
            quote = None

        broker = _accounts_port(b).fo_brokers().get(acct.id)
        if broker is None:
            errors.append("broker: 앱키가 없어 브로커를 만들지 못했습니다")
            out["orderable"] = out["balance"] = None
            balance = None
        else:
            if front is not None:
                price = Decimal(quote["price"]) if quote and quote.get("price") else None
                step(
                    "orderable",
                    lambda: _orderable_json(
                        broker.orderable(front.code, "BUY", price, "LIMIT" if price else "MARKET")
                    ),
                )
            else:
                out["orderable"] = None
            balance = step("balance_raw", lambda: broker.get_balance())
            out.pop("balance_raw", None)
            out["balance"] = _balance_json(balance) if balance is not None else None

        verified: list[str] = []
        # 마스터·시세·주문가능·잔고 중 하나라도 실패하면 검증 성공 상태를 남기지 않는다(Codex P1-4)
        if balance is not None and not errors:
            for v in verified_vehicles(balance):
                if vehicle is not None and v != vehicle:
                    continue
                control.set_flag(conn, executor.verified_key(settings, acct, v), CONTRACT_VERSION)
                verified.append(v)
        out["verified"] = verified
        out["errors"] = errors
    finally:
        conn.close()
    typer.echo(json.dumps(out, ensure_ascii=False))
    if errors:
        raise typer.Exit(code=1)


@fo_app.command("session")
@config_errors
def session_cmd(
    dry_run: bool = typer.Option(False, "--dry-run", help="판정·기록만(주문 없음)"),
    asof: str | None = typer.Option(
        None, "--asof", help="기준 시각 ISO (now − fo.max_input_age_s 보다 과거면 dry-run 강제)"
    ),
) -> None:
    """파생 세션 1회: `_fo_inputs` 수집·저장 → fo 세션 → `_fo_decision`.
    `FAKE_CLAUDE=1` 이면 subprocess 없이 고정 FLAT HOLD. 결과 JSON 한 줄, 세션 실패면 exit 1."""
    at = datetime.fromisoformat(asof) if asof else None
    if at is not None and at.tzinfo is None:
        at = at.replace(tzinfo=KST)
    b = boot(ports=True, log_name="fo")
    try:
        try:
            master = _master()
        except Exception as e:  # noqa: BLE001
            typer.echo(f"경고: 마스터 적재 실패({type(e).__name__}) — 빈 목록", err=True)
            master = []
        out = fo_session.run(
            b.settings, b.conn, ports=b.ports, master=master, dry_run=dry_run,
            notifier=make_notifier(b.secrets), asof=at, state=paths.state_dir(),
        )  # fmt: skip  (운영일은 asof 의 KST 날짜)
    finally:
        b.conn.close()
    typer.echo(json.dumps(out, ensure_ascii=False, default=str))
    if out.get("error"):
        raise typer.Exit(code=1)


@fo_app.command("order-test")
@config_errors
def order_test_cmd(
    account: str = typer.Option(..., "--account", help="모의 선물옵션 계좌 id (실전 거부)"),
    vehicle: str = typer.Option(..., "--vehicle", help="FUTURES|OPTION_BUY|OPTION_SELL"),
    wait_s: float = typer.Option(30.0, "--wait", help="체결 확정 대기(초)"),
) -> None:
    """실기 2 전용(거부 목록): 1계약 진입 → 체결 확정 → 잔고로 방향·종류·NAV·증거금 필드 확인 →
    즉시 청산 → 체결 확정. 전부 통과하면 그 수단의 `fo_verified` 플래그를 연다. 원장 미기록."""
    if vehicle not in VEHICLES:
        raise ConfigError(f"--vehicle 은 {'|'.join(VEHICLES)} 중 하나")
    b = boot(ports=True, log_name="fo")
    settings, conn = b.settings, b.conn
    try:
        acct = _find_fo_account(b, account)
        if acct.env != "paper":
            typer.echo("오류: fo order-test 는 모의(paper) 계좌에서만 실행합니다", err=True)
            raise typer.Exit(code=1)
        broker = _fo_broker(b, acct)
        out = fo_order_test.run(
            broker, _market_client(b), _master(), settings, vehicle,
            today=datetime.now(KST).date(), wait_s=wait_s,
        )  # fmt: skip
        if out["ok"]:
            key = executor.verified_key(settings, acct, vehicle)
            control.set_flag(conn, key, CONTRACT_VERSION)
            out["verified_flag"] = key
    finally:
        conn.close()
    typer.echo(json.dumps(out, ensure_ascii=False, default=str))
    if not out["ok"]:
        raise typer.Exit(code=1)


@fo_app.command("status")
@config_errors
def status_cmd() -> None:
    """파생 포지션·오늘 결정·한도 상태(원장만 읽음, 텔레그램 `/fo` 와 같은 줄)."""
    b = boot(settings=True, conn=True)
    try:
        typer.echo("\n".join(fo_status.lines(b.conn, datetime.now(UTC))))
    finally:
        b.conn.close()


@fo_app.command("monitor")
@config_errors
def monitor_cmd(
    once: bool = typer.Option(False, "--once", help="한 번만 돌고 끝"),
    dry_run: bool = typer.Option(False, "--dry-run", help="판정·기록만(주문·취소 없음)"),
    interval: int = typer.Option(60, "--interval", min=5, help="반복 간격(초)"),
) -> None:
    """파생 모니터(거부 목록 — 자동 주문). 데몬 `kr_fo_monitor` 와 같은 `engine.tick`.
    반복 모드는 파생 정규장(08:45~15:45) 밖에서 쉰다."""
    b = boot(ports=True, log_name="fo")
    try:
        ctx = _exec_ctx(b, dry_run=dry_run)
        while True:
            if once or is_fo_open(ctx.now()):
                out = engine.tick(ctx)
                typer.echo(json.dumps(out, ensure_ascii=False, default=str))
            if once:
                break
            time.sleep(interval)
    finally:
        b.conn.close()


@fo_app.command("adopt")
@config_errors
def adopt_cmd(
    account: str = typer.Option(..., "--account"),
    position: int = typer.Option(..., "--position", help="unknown 포지션 id"),
    side: str = typer.Option(..., "--side", help="LONG|SHORT (계약 보유 방향)"),
    kind: str = typer.Option(..., "--kind", help="FUTURES|CALL|PUT"),
    qty: int = typer.Option(..., "--qty", min=1),
    stop_pt: float = typer.Option(..., "--stop-pt", help="기초지수 손절선"),
) -> None:
    """사람의 승계: 브로커 잔고 행(코드·방향·종류·수량)과 **일치할 때만** unknown → held."""
    b = boot(ports=True, log_name="fo")
    try:
        acct = _find_fo_account(b, account)
        broker = _fo_broker(b, acct)
        pos = store.get_position(b.conn, position)
        contract = None
        try:
            contract = next((c for c in _master() if pos and c.code == pos.code), None)
        except Exception as e:  # noqa: BLE001 — 마스터 없으면 최종거래일 없이 승계(경고)
            typer.echo(f"경고: 마스터 없음({type(e).__name__}) — 최종거래일 미기록", err=True)
        ltd = fo_master.last_trade_date(contract, None) if contract is not None else None
        ok = positions.adopt(
            b.conn, position_id=position, side=side, kind=kind, qty=qty,
            stop_pt=Decimal(str(stop_pt)), balance=broker.get_balance(), account_id=acct.id,
            last_trade_date=ltd,
        )  # fmt: skip
        b.conn.commit()
    finally:
        b.conn.close()
    typer.echo(json.dumps({"position_id": position, "adopted": ok}, ensure_ascii=False))
    if not ok:
        raise typer.Exit(code=1)


@fo_app.command("close")
@config_errors
def close_cmd(
    account: str = typer.Option(..., "--account"),
    position: int = typer.Option(..., "--position"),
    confirm: str = typer.Option("", "--confirm", help="CLOSE 를 적어야 실행"),
) -> None:
    """수동 청산(거부 목록 — 사용자만): 진입 회수 → 잔고 재조회 → 청산 다리. unknown 포지션은
    먼저 `fo adopt` 로 승계해야 한다."""
    if confirm != "CLOSE":
        raise ConfigError("--confirm CLOSE 가 있어야 청산합니다")
    b = boot(ports=True, log_name="fo")
    try:
        ctx = _exec_ctx(b, dry_run=False)
        acct = executor.account_of(ctx, account)
        pos = store.get_position(b.conn, position)
        if acct is None or pos is None or pos.account_id != acct.id:
            raise ConfigError(f"계좌 {account} 의 포지션 {position} 이 없습니다")
        out = executor.reduce(ctx, acct, pos, intent="CLOSE", qty=None, reason="수동 청산(CLI)")
    finally:
        b.conn.close()
    typer.echo(json.dumps(out, ensure_ascii=False, default=str))
