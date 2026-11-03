"""scan 사용자 대면 문구(테마 열기·사건 감지). 공용 틀은 커널 `messages.render`."""

from __future__ import annotations

from trader.kernel.notify.messages import render


def theme_heat(*, market: str, themes: list[dict]) -> str:
    """테마 열기 상위 테마가 바뀌었을 때 (Phase 25)."""
    lines = []
    for th in themes:
        names = ", ".join(
            (f"{t['name']}({t['ticker']})" if t.get("name") else t["ticker"])
            for t in (th.get("tickers") or [])[:3]
        )
        cat = "촉매 있음" if th.get("catalyst_ok") else "촉매 없음(커뮤니티뿐)"
        lines.append(f"{th['rank']}. {theme_name(th['theme'])} · 점수 {th['score']:.2f} · {cat}")
        if names:
            lines.append(f"   {names}")
    return render("테마 열기", market, lines)


def theme_name(theme: str) -> str:
    """'섹터:반도체' → '반도체(섹터)', 'kw:로봇' → '로봇(뉴스 키워드)'."""
    kind, _, name = theme.partition(":")
    return f"{name}({'섹터' if kind == '섹터' else '뉴스 키워드'})" if name else theme


def event_detected(*, market: str, event: dict) -> str:
    """사건 수혜(2026-10-27): 오늘 처음 감지된 사건 — 알림만, 매수는 다음 scan 의 분석·게이트."""
    kind = "단기 사건" if event.get("kind") == "shock" else "구조적 흐름"
    tickers = ", ".join(
        f"{t.get('name')}({t.get('role')})" for t in (event.get("tickers") or [])[:6]
    )
    return render(
        "사건 감지",
        f"{market} · {event.get('label')} ({kind}, 뉴스 {event.get('hits')}건)",
        [
            *[f"· {n.get('title')}" for n in (event.get("news") or [])[:3]],
            f"수혜 후보: {tickers}" if tickers else "",
            "다음 scan 에서 scout 가 이 묶음 안에서 고르고, 분석·리스크 게이트를 거쳐요",
        ],
    )


def disclosure(*, market: str, label: str, title: str, url: str, action: str) -> str:
    """중요 공시(critical, Phase 25) — 보유·유니버스 종목. action 은 '재분석 요청 #n' 등."""
    return render("중요 공시", f"{market} {label}", [title, url, action])
