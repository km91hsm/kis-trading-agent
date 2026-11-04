"""fo 테스트 공용 헬퍼(옛 `tests/fo/test_positions.py`·`test_executor.py` 의 결합 헬퍼 분리). 복구·
실행기·체결·엔진 테스트가 import 한다: `_bal`(잔고)·`_row`(브로커 보유 행)·`_state`(주문 상태)·
`_open`(진입 다리 예약)·`_recover`(전송 유예가 지난 시점의 복구), 실행기 헬퍼 `account`·`balance`·
`holding`·`settings`·`decision`·`env`·`make_ctx`·`ckpt`·`state`·`_held_position`(옛 그대로),
입력·세션·도구 헬퍼 `_candles`·`_master_with_chain`·`_market`·`_ctx`·`_fo`·`_tool_ctx`·
`_reports`·`_debate`·`_ready`·`_env`·`_two_accounts`(옛 `test_inputs`·`test_tools_fo`)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from tests.features.accounts.conftest import make_account
from tests.features.fo._gate import NAV, contract, option_quote, quote
from tests.features.fo.conftest import ACCT, NOW, TODAY, Notes
from trader.features.accounts import api as accounts_api
from trader.features.fo import executor, positions, store
from trader.features.fo.schemas import FoDecision, FoDecisionEnvelope
from trader.features.fo.settings import FoCfg
from trader.features.fo.store import ReservePlan
from trader.features.fo.types import FoExecContext
from trader.features.regime import ports as regime_ports
from trader.kernel.broker.fake import FakeFoBroker, FakeMarket
from trader.kernel.broker.fo import CONTRACT_VERSION
from trader.kernel.broker.models import (
    Candle,
    FoBalance,
    FoCash,
    FoOrderable,
    FoOrderState,
    FoPosition,
)
from trader.kernel.cli.boot import build_registry
from trader.kernel.config.loader import load
from trader.kernel.ledger import control
from trader.kernel.registry import discover
from trader.kernel.session.run_store import market_dir, read_checkpoint

__all__ = [
    "ACCT", "MASTER", "NOW", "T0", "TODAY", "_bal", "_held_position", "_open", "_recover", "_row",
    "_state", "account", "balance", "ckpt", "decision", "env", "holding", "make_ctx", "settings",
    "state", "D", "_candles", "_master_with_chain", "_market", "_ctx", "_fo", "_tool_ctx",
    "_reports", "_debate", "_ready", "_env", "_two_accounts", "ports_for",
]  # fmt: skip


def _cash() -> FoCash:
    return FoCash(
        deposit=Decimal("1"), orderable_cash=Decimal("1"), orderable_total=Decimal("1"),
        margin_total=Decimal("0"), add_margin=Decimal("0"), nav=Decimal("10000000"),
        realized_today=Decimal("0"), unrealized=Decimal("0"), requested_at=NOW, received_at=NOW,
    )  # fmt: skip


def _bal(*rows: FoPosition, unverified=()) -> FoBalance:
    return FoBalance(positions=list(rows), unverified=list(unverified), cash=_cash())


def _row(code="105W11", kind="FUTURES", side="LONG", qty=2) -> FoPosition:
    return FoPosition(
        code=code, name=None, kind=kind, contract_side=side, qty=qty, avg_price=Decimal("350"),
        current_price=Decimal("351"), pnl=Decimal("0"), sellable_qty=qty,
    )  # fmt: skip


def _state(order_no, *, order_qty, filled, remaining=0, rejected=0, cancelled=0, state="filled"):
    terminal = (remaining == 0 or state in ("cancelled", "rejected")) and (
        filled + rejected + cancelled == order_qty
    )
    return FoOrderState(
        order_no=order_no, order_qty=order_qty, filled_qty=filled, remaining_qty=remaining,
        rejected_qty=rejected, cancelled_qty=cancelled, terminal=terminal, state=state,
    )  # fmt: skip


def _open(conn, qty=2, *, status="entering", max_positions=1, **over):
    """진입 다리를 예약하고 (position, leg) 를 돌려준다."""
    with store.tx(conn):
        r = store.reserve(
            conn,
            ACCT,
            ReservePlan(
                intent="OPEN", round=0, qty=qty, price=Decimal("350.5"), order_type="LIMIT",
                new_position={
                    "account_id": ACCT, "code": "105W11", "kind": "FUTURES",
                    "contract_side": "LONG", "direction": "LONG", "vehicle": "FUTURES",
                    "nav_at_open": Decimal("10000000"), "stop_pt": Decimal("345"),
                    "approved_mult": 1.5, "leverage_used": 1.2, **over,
                },
                new_status=status,
            ),
            max_positions=max_positions,
            max_contracts=4,
        )  # fmt: skip
    assert r.ok, r.reasons
    return store.get_position(conn, r.position_id), store.get_leg(conn, r.leg_id)


def _recover(conn, balance, *, states=None, fills=None, raise_state=False):
    conn.commit()  # 테스트가 연 암묵 트랜잭션을 닫는다 — `store.tx` 는 바깥 트랜잭션에 합류만 한다

    def order_state_of(no, d):
        if raise_state:
            raise RuntimeError("조회 실패")
        return (states or {}).get(no) or _state(no, order_qty=0, filled=0, state="open")

    return positions.recover(
        conn,
        account_id=ACCT,
        balance=balance,
        order_state_of=order_state_of,
        fills_of=lambda d: list(fills or []),
        today=TODAY,
        now=NOW + timedelta(minutes=5),  # 전송 뒤 유예(SENDING_GRACE_S)가 지난 복구
    )


# --- 실행기 헬퍼(옛 `tests/fo/test_executor.py`) --------------------------------------------

T0 = NOW  # 10:30 KST
MASTER = [contract("105W11", "FUTURES"), contract("205W11350", "CALL", Decimal("350"))]


def account(env="paper", mode="auto", asset="fo"):
    return make_account(ACCT if env == "paper" else "real-fo", env, mode, ["KR"],
                        acnt_prdt_cd="03", asset=asset)  # fmt: skip


def cash(nav=NAV, add_margin=Decimal(0), t=T0) -> FoCash:
    return FoCash(
        deposit=nav, orderable_cash=Decimal("50000000"), orderable_total=Decimal("50000000"),
        margin_total=Decimal(0), add_margin=add_margin, nav=nav, realized_today=Decimal(0),
        unrealized=Decimal(0), requested_at=t, received_at=t,
    )  # fmt: skip


def holding(code="105W11", kind="FUTURES", side="LONG", qty=2) -> FoPosition:
    return FoPosition(
        code=code, name=code, kind=kind, contract_side=side, qty=qty, avg_price=Decimal("350"),
        current_price=Decimal("350.5"), pnl=Decimal(0), sellable_qty=qty,
    )  # fmt: skip


def balance(positions=(), nav=NAV, **kw) -> FoBalance:
    return FoBalance(positions=list(positions), unverified=[], cash=cash(nav=nav, **kw))


def settings(**fo):
    """설정 전체(settings.yaml 없으면 기본값) + `fo`(enabled·hold_overnight=False + 덮어쓰기)."""
    base = {"enabled": True, "hold_overnight": False}
    base.update(fo)
    s = load("settings", build_registry(discover()))
    return s.model_copy(update={"fo": FoCfg(**base)})


def decision(**over) -> FoDecision:
    base = dict(
        intent="OPEN", direction="LONG", vehicle="FUTURES", target_leverage=1.0,
        order_type="LIMIT", entry_price=350.5, stop_pt=345.0, take_profit_pt=360.0,
        confidence=0.7, rationale="t", horizon_days=5,
    )  # fmt: skip
    base.update(over)
    return FoDecision(**base)


def env(d=None, **over) -> FoDecisionEnvelope:
    return FoDecisionEnvelope(decision=d or decision(), request_id="r1", **over)


def ports_for(s, accounts: list) -> dict:
    """`Boot.ports` 모양: accounts(모드는 실제 `account_mode`) + regime(실제 구현)."""
    acc = SimpleNamespace(
        fo_accounts=lambda: list(accounts), fo_brokers=dict, accounts=list, brokers=dict,
        market_client=lambda: None, policy=accounts_api.account_mode,
        paused=accounts_api.is_paused, holiday_fn=lambda: (lambda m, d: False),
    )  # fmt: skip
    return {"accounts": acc, "regime": regime_ports.build_port(SimpleNamespace(settings=s))}


def make_ctx(conn, *, broker=None, market=None, acct=None, dry_run=False, now=T0, **fo):
    acct = acct or account()
    broker = broker or FakeFoBroker(acct, balance=balance(), now=lambda: now,
                                    orderable=FoOrderable(tot_qty=10, lqd_qty=0, ord_qty=10,
                                                          cash=None, bass_idx=None))  # fmt: skip
    market = market or FakeMarket(fo_quotes={"105W11": quote(), "205W11350": option_quote()},
                                  kospi200=(Decimal("350"), now))  # fmt: skip
    s = settings(**fo)
    for v in ("FUTURES", "OPTION_BUY", "OPTION_SELL"):
        control.set_flag(conn, executor.verified_key(s, acct, v), CONTRACT_VERSION)
    return FoExecContext(
        settings=s, accounts=[acct], conn=conn, brokers={acct.id: broker}, market_client=market,
        master=MASTER, notifier=Notes(), now=lambda: now, dry_run=dry_run,
        ports=ports_for(s, [acct]),
    )  # fmt: skip


def ckpt(name: str, d: date = TODAY):
    return read_checkpoint(market_dir(d, "KR"), name)


def state(order_no, *, order_qty, filled, remaining, cancelled=0, terminal, st="open"):
    return FoOrderState(
        order_no=order_no, order_qty=order_qty, filled_qty=filled, remaining_qty=remaining,
        rejected_qty=0, cancelled_qty=cancelled, terminal=terminal, state=st,
    )  # fmt: skip


def _held_position(conn, ctx, qty=2, **over):
    """보유 중 포지션 1개를 원장에 만든다(브로커 잔고도 같은 수량)."""
    fields = dict(
        account_id=ACCT, code="105W11", kind="FUTURES", contract_side="LONG", direction="LONG",
        vehicle="FUTURES", status="held", qty_target=qty, qty_held=qty, leverage_used=0.35,
        stop_pt=Decimal("345"), nav_at_open=NAV, decision_date=TODAY.isoformat(),
    )  # fmt: skip
    fields.update(over)
    with store.tx(conn):
        pid = store.insert_position(conn, **fields)
    ctx.brokers[ACCT].balance = balance([holding(qty=qty, kind=fields["kind"],
                                                 code=fields["code"],
                                                 side=fields["contract_side"])])  # fmt: skip
    return store.get_position(conn, pid)


# --- 입력·세션·도구 헬퍼(옛 `tests/fo/test_inputs.py`·`orchestrator/test_tools_fo.py`) -------

D = TODAY


def _candles(n=30):
    """D 이전 n 일의 합성 일봉(종가 341 → 341+n-1)."""
    return [
        Candle(
            date=D - timedelta(days=n - i), open=Decimal(340 + i), high=Decimal(342 + i),
            low=Decimal(338 + i), close=Decimal(341 + i), volume=100 + i,
        )  # fmt: skip
        for i in range(n)
    ]


def _master_with_chain(strikes=range(330, 372, 2)):
    cs = [contract("105W11", "FUTURES")]
    for k in strikes:
        cs.append(contract(f"205W11{k}", "CALL", Decimal(k)))
        cs.append(contract(f"305W11{k}", "PUT", Decimal(k)))
    return cs


def _market(fail=None, chain_strikes=range(330, 372, 2), now=NOW):
    fo_quotes = {"105W11": quote()}
    for k in chain_strikes:
        for kind, pre, dl in (("CALL", "205", 0.5), ("PUT", "305", -0.5)):
            fo_quotes[f"{pre}W11{k}"] = quote(
                code=f"{pre}W11{k}", kind=kind, price="5.00", delta=dl, iv=18.0,
                strike=Decimal(k), basis=None, volume=10 if kind == "CALL" else 12,
            )  # fmt: skip
    return FakeMarket(
        candles={"069500": _candles()}, fo_quotes=fo_quotes, fo_candles={"105W11": _candles()},
        kospi200=(Decimal("350"), now), fail=fail, now=lambda: now,
    )  # fmt: skip


def _ctx(conn, *, market=None, master=None, now=NOW, **fo):
    """입력·세션 테스트의 `FoExecContext`(검증 플래그 없음, 포트는 `ports_for`)."""
    acct = account()
    broker = FakeFoBroker(acct, balance=balance(), now=lambda: now)
    s = settings(**fo)
    return FoExecContext(
        settings=s, accounts=[acct], conn=conn, brokers={acct.id: broker},
        market_client=market or _market(now=now), master=master or _master_with_chain(),
        now=lambda: now, ports=ports_for(s, [acct]),
    )  # fmt: skip


def _fo(conn, *, now=NOW, asof=None, enabled=True):
    """검증 플래그를 켠 실행 컨텍스트 + 저장된 `_fo_inputs`."""
    from trader.features.fo import inputs as inputs_mod

    fo = _ctx(conn, now=now, enabled=enabled)
    for v in ("FUTURES", "OPTION_BUY", "OPTION_SELL"):  # 실기 검증 플래그(없으면 신규 거부)
        control.set_flag(conn, executor.verified_key(fo.settings, fo.accounts[0], v),
                         CONTRACT_VERSION)  # fmt: skip
    inp = inputs_mod.build_inputs(fo, today=D, asof=asof or now, accounts=fo.accounts)
    inputs_mod.save_inputs(inp, today=D, state=None)
    return fo, inp


def _tool_ctx(conn, fo, inp, now=NOW, *, dry_run=False):
    from trader.features.fo import tools

    ctx = tools.FoToolContext(settings=fo.settings, conn=conn, now=lambda: now, fo=fo,
                              dry_run=dry_run)  # fmt: skip
    ctx._inputs = inp  # type: ignore[attr-defined]
    return ctx


def _reports(ctx, roles=("technical", "sentiment")):
    from tests.features.spot.helpers import ok_report
    from trader.features.fo import tools

    for r in roles:
        out = tools.fo_write(
            ctx, date=D, stage=f"_fo_analyst_{r}", payload=ok_report(ticker="KOSPI200", role=r),
            agent=f"analyst-{r}",
        )  # fmt: skip
        assert out["ok"], out


def _debate(ctx, rounds=(0,)):
    from tests.features.spot.helpers import ok_turn
    from trader.features.fo import tools

    for n in rounds:
        for side in ("bull", "bear"):
            out = tools.fo_write(
                ctx, date=D, stage=f"_fo_debate_r{n}_{side}", payload=ok_turn(side, n),
                agent=f"researcher-{side}",
            )  # fmt: skip
            assert out["ok"], out


def _ready(ctx):
    from trader.features.fo import tools

    _reports(ctx)
    _debate(ctx)
    assert tools.fo_summarize(ctx, date=D)["ok"]


def _env(ctx, **dec):
    base = dict(
        intent="OPEN", direction="LONG", vehicle="FUTURES", target_leverage=1.0,
        order_type="LIMIT", entry_price=350.5, stop_pt=345.0, take_profit_pt=360.0,
        confidence=0.7, rationale="t", horizon_days=5,
    )  # fmt: skip
    base.update(dec)
    return {"decision": base, "request_id": ctx._inputs["meta"]["request_id"]}


def _two_accounts(conn):
    """파생 계좌 2개(paper-fo·paper-fo2) + 준비된 단계 — 계좌별 격리·재개 테스트용."""
    fo, inp = _fo(conn)
    second = make_account("paper-fo2", "paper", "auto", ["KR"], acnt_prdt_cd="03", asset="fo")
    fo.accounts.append(second)
    fo.brokers[second.id] = FakeFoBroker(second, balance=balance(), now=lambda: NOW,
                                         orderable=fo.brokers[ACCT].orderable_result)  # fmt: skip
    fo.ports = ports_for(fo.settings, list(fo.accounts))
    for v in ("FUTURES", "OPTION_BUY", "OPTION_SELL"):
        control.set_flag(conn, executor.verified_key(fo.settings, second, v), CONTRACT_VERSION)
    ctx = _tool_ctx(conn, fo, inp)
    _ready(ctx)
    return ctx
