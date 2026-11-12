"""SEC EDGAR 수집기: 티커→CIK 맵(7일 캐시), companyfacts, DART 와 같은 키의 재무비율.

SEC 는 User-Agent "<이름> <이메일>" 을 요구한다. `filed <= asof` 인 10-K 값만 쓴다(룩어헤드 방지).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from trader.features.ingest.dart import RATIO_KEYS, basic_ratios
from trader.kernel.config.time import MARKET_TZ
from trader.kernel.platform.logging_setup import quiet_httpx

logger = logging.getLogger("trader.sources")

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
CACHE_DAYS = 7

# 공통 키 → (taxonomy, 후보 태그들, 기간형 여부)
_TAGS: dict[str, tuple[str, tuple[str, ...], bool]] = {
    "매출액": (
        "us-gaap",
        ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"),
        True,
    ),
    "영업이익": ("us-gaap", ("OperatingIncomeLoss",), True),
    "당기순이익": ("us-gaap", ("NetIncomeLoss", "ProfitLoss"), True),
    "자산총계": ("us-gaap", ("Assets",), False),
    "부채총계": ("us-gaap", ("Liabilities",), False),
    "자본총계": (
        "us-gaap",
        (
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        ),
        False,
    ),
}
_SHARES_TAG = ("dei", "EntityCommonStockSharesOutstanding", "shares")


class EdgarError(RuntimeError):
    """EDGAR HTTP 실패."""


def _d(s: str | None) -> date | None:
    try:
        return date.fromisoformat(str(s)) if s else None
    except ValueError:
        return None


class EdgarClient:
    def __init__(
        self,
        user_agent: str,
        cache_dir: Path,
        *,
        http: httpx.Client | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        quiet_httpx()
        self._headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
        self._cache_dir = cache_dir
        self._owns_http = http is None  # 직접 만든 연결만 닫는다
        self._http = http or httpx.Client(timeout=15)
        self._now = now
        self._tickers: dict[str, Any] | None = None

    def close(self) -> None:
        """직접 만든 HTTP 연결을 닫는다(주입받은 것은 건드리지 않는다). 잡마다 새 클라이언트를
        만드는 호출처가 끝에 부른다."""
        if self._owns_http:
            self._http.close()

    def _get(self, url: str) -> Any:
        resp = self._http.get(url, headers=self._headers)
        if resp.status_code != 200:
            raise EdgarError(f"EDGAR HTTP {resp.status_code}: {url}")
        return resp.json()

    def _load_tickers(self) -> dict[str, Any]:
        if self._tickers is not None:
            return self._tickers
        cache = self._cache_dir / "edgar_tickers.json"
        now = self._now()
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
            if now - datetime.fromisoformat(cached["fetched_at"]) < timedelta(days=CACHE_DAYS):
                self._tickers = cached["map"]
                return self._tickers
        except (OSError, ValueError, KeyError, TypeError):
            pass
        data = self._get(TICKERS_URL)
        mapping: dict[str, Any] = {}
        for v in data.values() if isinstance(data, dict) else data:
            if isinstance(v, dict) and v.get("ticker") and v.get("cik_str") is not None:
                mapping[str(v["ticker"]).upper()] = {
                    "cik": f"{int(v['cik_str']):010d}",
                    "name": str(v.get("title") or "").strip(),
                }
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"fetched_at": now.isoformat(), "map": mapping}), encoding="utf-8"
        )
        tmp.replace(cache)
        self._tickers = mapping
        return mapping

    def cik(self, symbol: str) -> str | None:
        return (self._load_tickers().get(symbol.upper()) or {}).get("cik")

    def company_name(self, symbol: str) -> str | None:
        return (self._load_tickers().get(symbol.upper()) or {}).get("name") or None

    def companyfacts(self, cik: str) -> dict:
        """메모하지 않는다 — 데몬은 클라이언트를 기동 때 한 번 만들어 재사용하므로 프로세스 수명
        메모는 새 10-Q 를 영영 반영하지 못한다. 한 수집 안에서 비율·history 가 같이 쓰려면 호출자가
        facts 를 들고 넘긴다."""
        return self._get(FACTS_URL.format(cik=cik))

    def submissions(self, cik: str) -> dict:
        """최근 제출 목록(Phase 25 공시 중요도). `filings.recent` 의 병렬 배열(form·filingDate·
        accessionNumber·primaryDocument·items). 메모하지 않는다."""
        return self._get(SUBMISSIONS_URL.format(cik=cik))


# 정정 공시(/A)는 같은 결산일의 원본을 덮는다 — 정렬이 (end, filed) 내림차순이라 asof 까지 제출된
# 최신 제출분이 먼저 온다. 전환기 보고서(10-KT·10-QT)는 기간 길이 필터가 걸러 낸다.
ANNUAL_FORMS = ("10-K", "10-K/A")
QUARTER_FORMS = ("10-Q", "10-Q/A")


def _annual_entries(entries: list[dict], asof: date, *, duration: bool) -> list[dict]:
    """asof 이전에 제출된 10-K(정정 포함) 연간 값만, (end, filed) 내림차순 — 정정분이 앞."""
    out = []
    for e in entries:
        if str(e.get("form", "")) not in ANNUAL_FORMS:
            continue
        filed = _d(e.get("filed"))
        end = _d(e.get("end"))
        if filed is None or end is None or filed > asof:
            continue
        if duration:
            start = _d(e.get("start"))
            if start is None or not (300 <= (end - start).days <= 400):
                continue
        out.append(e)
    out.sort(key=lambda e: (e["end"], e.get("filed", "")), reverse=True)
    return out


ANNUAL_DAYS = (300, 400)
QUARTER_DAYS = (60, 120)
HISTORY_YEARS = 3


def _unit_entries(all_facts: dict, tax: str, tag: str) -> list[dict]:
    return (((all_facts.get(tax) or {}).get(tag) or {}).get("units") or {}).get("USD") or []


def _values_at(
    all_facts: dict, *, forms: tuple[str, ...], end: str, span: tuple[int, int], asof: date
) -> tuple[dict[str, Decimal], str | None, str | None]:
    """한 결산일(end)의 공통 키 값·제출일·form. 같은 end 에 원본과 정정(/A)이 있으면 asof 까지
    제출된 것 중 가장 늦게 제출된 값을 쓴다. 기간형은 (end − start) 일수가 span 안."""
    fin: dict[str, Decimal] = {}
    filed_at: str | None = None
    form_at: str | None = None
    end_d = _d(end)
    for key, (tax, tags, duration) in _TAGS.items():
        for tag in tags:
            hit = None
            for ent in _unit_entries(all_facts, tax, tag):
                if str(ent.get("form", "")) not in forms or str(ent.get("end", "")) != end:
                    continue
                filed = _d(ent.get("filed"))
                if filed is None or filed > asof:
                    continue
                if duration:
                    start = _d(ent.get("start"))
                    if start is None or end_d is None:
                        continue
                    if not (span[0] <= (end_d - start).days <= span[1]):
                        continue
                if hit is None or str(ent.get("filed")) > str(hit.get("filed")):
                    hit = ent
            if hit is not None:
                fin[key] = Decimal(str(hit["val"]))
                if filed_at is None or str(hit.get("filed")) > filed_at:
                    filed_at, form_at = str(hit.get("filed")), str(hit.get("form"))
                break
    return fin, filed_at, form_at


def _report_ends(
    all_facts: dict, *, forms: tuple[str, ...], span: tuple[int, int] | None, asof: date
) -> list[str]:
    """asof 까지 제출된 보고서(forms)의 end 날짜(내림차순, 중복 없음). 기간형 태그로 판단한다."""
    ends: set[str] = set()
    for tax, tags, duration in _TAGS.values():
        if not duration:
            continue
        for tag in tags:
            for ent in _unit_entries(all_facts, tax, tag):
                if str(ent.get("form", "")) not in forms:
                    continue
                filed, start, end = _d(ent.get("filed")), _d(ent.get("start")), _d(ent.get("end"))
                if filed is None or end is None or filed > asof:
                    continue
                if span is not None:
                    if start is None or not (span[0] <= (end - start).days <= span[1]):
                        continue
                ends.add(end.isoformat())
    return sorted(ends, reverse=True)


def financial_history_us(facts: dict, asof: date) -> dict[str, Any]:
    """DART `financial_history` 와 같은 꼴: 3개년 10-K(`years`, end 연도 키, `_전기` 없음) +
    최신 10-K 뒤의 최근 10-Q(`latest_quarter`: form·end·filed + 공통 키). asof 까지 제출된 것만."""
    all_facts = facts.get("facts") or {}
    years: dict[int, dict[str, Decimal]] = {}
    annual_ends = _report_ends(all_facts, forms=ANNUAL_FORMS, span=ANNUAL_DAYS, asof=asof)
    for end in annual_ends:
        if len(years) >= HISTORY_YEARS:
            break
        year = int(end[:4])
        if year in years:
            continue  # 결산월 변경으로 같은 연도에 10-K 가 둘이면 최신(end 가 늦은) 것만
        fin, _filed, _form = _values_at(
            all_facts, forms=ANNUAL_FORMS, end=end, span=ANNUAL_DAYS, asof=asof
        )
        if fin:
            # 1월 결산사(NVDA FY2026=2026-01-25)처럼 연도 키가 달력연도와 달라 end 를 함께 둔다
            years[year] = {**fin, "end": end}
    latest_quarter: dict[str, Any] | None = None
    floor = annual_ends[0] if annual_ends else ""
    for end in _report_ends(all_facts, forms=QUARTER_FORMS, span=QUARTER_DAYS, asof=asof):
        if end <= floor:
            break
        fin, filed, form = _values_at(
            all_facts, forms=QUARTER_FORMS, end=end, span=QUARTER_DAYS, asof=asof
        )
        if fin:
            latest_quarter = {"form": form or "10-Q", "end": end, "filed": filed, **fin}
            break
    return {"years": years, "latest_quarter": latest_quarter}


def financials_us(facts: dict, asof: date) -> tuple[dict[str, Decimal], int | None]:
    """companyfacts → 공통 키 재무제표(+`_전기`)와 보통주 수."""
    fin: dict[str, Decimal] = {}
    all_facts = facts.get("facts") or {}
    for key, (tax, tags, duration) in _TAGS.items():
        # 회사가 보고 태그를 바꾸면 옛 태그에 과거 값만 남는다 — 최신 결산일을 가진 태그를 쓴다
        # (같으면 _TAGS 순서가 앞선 태그, max 는 첫 최댓값을 돌려준다)
        candidates = [
            _annual_entries(_unit_entries(all_facts, tax, tag), asof, duration=duration)
            for tag in tags
        ]
        annual = max((c for c in candidates if c), key=lambda c: c[0]["end"], default=None)
        if annual is None:
            continue
        fin[key] = Decimal(str(annual[0]["val"]))
        latest_end = _d(annual[0]["end"])
        for prev in annual[1:]:
            prev_end = _d(prev["end"])
            if latest_end and prev_end and 300 <= (latest_end - prev_end).days <= 400:
                fin[f"{key}_전기"] = Decimal(str(prev["val"]))
                break
    tax, tag, unit = _SHARES_TAG
    shares_entries = (((all_facts.get(tax) or {}).get(tag) or {}).get("units") or {}).get(
        unit
    ) or []
    shares = None
    annual = _annual_entries(shares_entries, asof, duration=False)
    if annual:
        shares = int(annual[0]["val"])
    return fin, shares


def ratios_by_year_us(
    edgar: EdgarClient, symbol: str, years: list[int]
) -> dict[int, dict[str, float | None]]:
    """연도별 비율(피처 스토어용). 사업연도 Y 값은 Y+1 년 4월 1일까지 제출된 10-K 로만 만든다
    (KR 과 같은 룩어헤드 규칙 — 지연 제출분은 다음 해로 밀린다). companyfacts 는 1회만 받고,
    CIK 가 없거나 그 시점에 10-K 가 없는 연도는 빠진다. 주식수·가격은 없어 PER/PBR/PSR 은 None."""
    cik = edgar.cik(symbol)
    if not cik:
        return {}
    facts = edgar.companyfacts(cik)
    out: dict[int, dict[str, float | None]] = {}
    for y in years:
        fin, _shares = financials_us(facts, date(y + 1, 4, 1))
        if fin:
            out[y] = basic_ratios(fin, shares=None, price=None)
    return out


def ratios_from_facts(facts: dict, asof: date, price: Decimal | None) -> dict[str, float | None]:
    """이미 받은 companyfacts 로 비율 (수집 1회 안에서 history 와 같은 facts 를 쓴다)."""
    fin, shares = financials_us(facts, asof)
    return basic_ratios(fin, shares=shares, price=price)


def ratios_us(
    edgar: EdgarClient, symbol: str, asof: date, price: Decimal | None
) -> dict[str, float | None]:
    cik = edgar.cik(symbol)
    if not cik:
        return dict.fromkeys(RATIO_KEYS)
    return ratios_from_facts(edgar.companyfacts(cik), asof, price)


# 분석 입력에 넣는 제출 종류 — 내부자 거래(Form 4 등)처럼 건수만 많은 양식은 뺀다(/A 정정 포함).
FILING_FORMS = ("8-K", "10-K", "10-Q", "20-F", "6-K", "S-1", "S-3")


def _accepted_at(raw: Any) -> datetime | None:
    """접수 시각. `Z` 표기가 동부시간 벽시계인 사례가 있어 동부시간으로 읽는다 — 실제 UTC 라도
    더 늦은 시각으로 해석될 뿐이라 룩어헤드 쪽으로는 틀리지 않는다."""
    try:
        return datetime.fromisoformat(str(raw)[:19]).replace(tzinfo=MARKET_TZ["US"])
    except ValueError:
        return None


def filings_from_submissions(
    submissions: dict, cik: str, asof: datetime, days: int = 30
) -> list[dict]:
    """DART `disclosures` 와 같은 꼴의 최근 days 일 제출 목록(최신순). asof 뒤 접수분은 뺀다."""
    recent = (submissions.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []

    def column(name: str) -> list:
        return recent.get(name) or [""] * len(forms)

    asof_day = asof.astimezone(MARKET_TZ["US"]).date()
    since = asof_day - timedelta(days=days)
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}"
    out: list[dict] = []
    for form, filed_raw, accepted_raw, acc, doc, items_raw in zip(
        forms,
        column("filingDate"),
        column("acceptanceDateTime"),
        column("accessionNumber"),
        column("primaryDocument"),
        column("items"),
        strict=True,
    ):
        name = str(form)
        filed = _d(filed_raw)
        if name.removesuffix("/A") not in FILING_FORMS or filed is None:
            continue
        accepted = _accepted_at(accepted_raw)
        if not since <= filed <= asof_day or (accepted is not None and accepted > asof):
            continue
        items = [x.strip() for x in str(items_raw or "").split(",") if x.strip()]
        out.append(
            {
                "title": f"{name} (items {', '.join(items)})" if items else name,
                "filed": filed.isoformat(),
                "url": f"{base}/{str(acc).replace('-', '')}/{doc}",
            }
        )
    out.sort(key=lambda i: i["filed"], reverse=True)
    return out


def disclosures_us(edgar: EdgarClient, symbol: str, asof: datetime, days: int = 30) -> list[dict]:
    cik = edgar.cik(symbol)
    return filings_from_submissions(edgar.submissions(cik), cik, asof, days) if cik else []
