def test_auto_trip_scopes_to_account_manual_to_all(conn):
    """자동 발동은 발동한 계좌만 막고(모의 손실이 실전을 막지 않게), 수동 /kill 은 전 계좌."""
    from datetime import date
    from decimal import Decimal

    from trader.features.regime import killswitch

    kw = {"market": "KR", "today": date(2026, 10, 27), "limit_pct": 0.15}
    killswitch.observe(conn, account_id="paper-main", nav=Decimal("100"), **kw)
    assert killswitch.observe(conn, account_id="paper-main", nav=Decimal("80"), **kw)
    assert killswitch.reason(conn, "paper-main") and killswitch.reason(conn, "real-main") is None
    assert killswitch.reason(conn)  # 표시용은 켜져 있음
    killswitch.engage(conn, reason="/kill", source="telegram")
    assert killswitch.reason(conn, "real-main")


def test_auto_engage_does_not_shrink_manual_kill(conn):
    from trader.features.regime import killswitch

    killswitch.engage(conn, reason="/kill", source="telegram")
    killswitch.engage(conn, reason="자동", source="auto", account_id="paper-main")
    assert killswitch.state(conn)["accounts"] is None  # 전 계좌 그대로
    assert not conn.in_transaction


def test_unexplained_nav_jump_flags_flow_suspicion_without_touching_base(conn):
    """2-O16: 보유 종목 가격 변화로 설명되지 않는 NAV 변화(입출금 의심)는 기록만 하고 기준·판정은
    그대로 둔다. 가격 변화로 설명되는 변화·작은 잔차는 의심이 아니다."""
    from datetime import date
    from decimal import Decimal as D

    from trader.features.regime import killswitch

    kw = {"market": "KR", "today": date(2026, 10, 27), "limit_pct": 0.15}
    pos = [("005930", 10, D("100"))]  # 평가 1,000 + 현금 9,000
    killswitch.observe(conn, account_id="paper-main", nav=D("10000"), positions=pos, **kw)
    assert killswitch.flow_suspicion(conn, "paper-main", "KR") is None
    # 가격이 100 → 150: NAV +500 은 설명됨
    pos2 = [("005930", 10, D("150"))]
    killswitch.observe(conn, account_id="paper-main", nav=D("10500"), positions=pos2, **kw)
    assert killswitch.flow_suspicion(conn, "paper-main", "KR") is None
    # 가격 그대로인데 NAV 가 2,500 줄었다 — 출금 의심(잔차 −2,500 ≥ 5%)
    killswitch.observe(conn, account_id="paper-main", nav=D("8000"), positions=pos2, **kw)
    got = killswitch.flow_suspicion(conn, "paper-main", "KR")
    assert got is not None and D(got["residual"]) == D("-2500") and got["date"] == "2026-10-27"
    assert killswitch.reason(conn, "paper-main")  # 판정은 기준 그대로(−20%) — 그래서 특보로 알린다
    assert D(killswitch.control.get_flag(conn, "killswitch_base:paper-main:KR")["nav"]) == D(
        "10000"
    )
    # 사람이 기준을 다시 잡으면 의심 기록도 지운다
    killswitch.release(conn, note="출금 2,500 확인 — 기준 재설정")
    assert killswitch.flow_suspicion(conn, "paper-main", "KR") is None


def test_baseline_and_release_read_account_nav(conn):
    """Ruling P3: 기준은 `account_nav` 의 가장 이른 양수 NAV(0 은 건너뛴다), 해제는 (계좌·시장)별
    최신 NAV 로 기준을 다시 잡는다. 합성 값."""
    from datetime import date
    from decimal import Decimal as D

    from trader.features.regime import killswitch

    rows = [
        ("paper-main", "2026-10-26", "KR", "0"),
        ("paper-main", "2026-10-27", "KR", "1000"),
        ("paper-main", "2026-10-28", "KR", "1200"),
        ("paper-main", "2026-10-28", "US", "50"),
    ]
    conn.executemany(
        "INSERT INTO account_nav(account_id, date, market, nav, cash, created_at) "
        "VALUES (?, ?, ?, ?, '0', '2026-10-28T00:00:00+00:00')",
        rows,
    )
    conn.commit()
    assert killswitch._baseline(conn, "paper-main", "KR", D("900")) == D("1000")
    kw = {"market": "KR", "today": date(2026, 10, 28), "limit_pct": 0.15}
    assert killswitch.observe(conn, account_id="paper-main", nav=D("800"), **kw)  # −20%
    killswitch.release(conn, note="합성 시험 — 기준 재설정")
    base = killswitch.control.get_flag
    assert D(base(conn, "killswitch_base:paper-main:KR")["nav"]) == D("1200")
    assert D(base(conn, "killswitch_base:paper-main:US")["nav"]) == D("50")
    assert killswitch.reason(conn, "paper-main") is None
