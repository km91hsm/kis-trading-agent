# 정확한 거시 지표 원천 조사 — 2026-11-07

## 결론

**현재 가장 작은 개선은 기존 KIS `OVS_FX_DAILY`의 조회 TR을 재사용해 `N/SPX`와 `N/COMP`를 추가하고, FRED의 같은 지수 일별 종가를 대체 경로로 유지하는 것이다.** WTI 선물·ICE DXY·Investing 미국채 10년물과 정확히 같은 관측을 무료 공개 HTTP만으로 지속 수집할 수 있다는 근거는 확보하지 못했다. 이 세 항목은 미확인 원천을 성공으로 표시하지 말고, 실제 원천과 차이를 드러낸 대체 관측 또는 결측으로 전달한다. 이는 아래 문서·마스터·접근 검증에서 도출한 구현 권고다.

Investing.com은 데이터 제공업체 계약 때문에 공개 API를 제공하지 않는다고 명시한다. 해당 사이트의 화면이 검색 도구에서 읽히는 것과 로컬 데몬이 안정적으로 요청할 수 있는 것은 별개다. 이번 일반 HTTP 탐침은 5개 페이지 모두 `403`이었다. 숨은 브라우저나 헤더 변조로 우회하지 않았다. [Investing 공식 API 안내](https://www.investing-support.com/hc/en-us/articles/115005473825-Do-You-Offer-API-Access-at-Investing-com)

조사만 수행했다. 인증 API·비밀키·로그인 상태·실제 추론·주문·유료 서비스는 사용하지 않았고 수집기는 구현하지 않았다. 아래 KIS 코드 존재는 확인했으나 인증 응답 성공을 뜻하지 않는다.

## 요청 지표와 현재 대체값의 차이

| 요청 | 원본 상품/단위/시점 | 현재 FRED | 판정 |
|---|---|---|---|
| S&P500 | S&P 500 가격지수, 지수 포인트. Investing 화면은 `Real-time derived` 표시 | `SP500`: 가격지수의 일별 종가, 통상 16:00 ET | 같은 지수지만 장중/일별 시점 차이. 실시간 Investing 수집이라고 쓰지 않는다 |
| 나스닥종합 | NASDAQ Composite / `IXIC`, 지수 포인트 | `NASDAQCOM`: Composite 일별 종가, 통상 16:00 ET | 정확한 지수. Nasdaq100 / `NDX` / `QQQ`로 치환하지 않는다 |
| WTI | Investing Crude Oil 페이지는 선물 관련 계약월·만기·rollover 정보를 표시. USD/barrel | `DCOILWTICO`: EIA Cushing WTI 현물, 일별 USD/barrel | 현물 대체지표. 선물 계약과 동일하지 않다 |
| 달러지수 DXY | ICE의 6개 통화 지수. Investing 화면은 `Real-time derived` | `DTWEXBGS`: 연준 명목 광의 달러지수, 2006-01=100 | 구성·기준·수준이 다른 대체지표. exact DXY 아님 |
| 미국채 10년물 수익률 | Investing 페이지는 yield와 별도의 bond price·coupon·maturity를 표시 | `DGS10`: 10년 constant maturity 수익률, percent, 일별 | 동일 만기 영역의 대체 관측. 같은 채권/같은 시각의 장중 호가라고 확정할 수 없음 |

근거: [Investing SPX](https://www.investing.com/indices/us-spx-500), [Composite](https://www.investing.com/indices/nasdaq-composite), [WTI](https://www.investing.com/commodities/crude-oil), [DXY](https://www.investing.com/indices/usdollar), [US10Y](https://www.investing.com/rates-bonds/u.s.-10-year-bond-yield); [FRED SP500](https://fred.stlouisfed.org/series/SP500), [NASDAQCOM](https://fred.stlouisfed.org/series/NASDAQCOM), [DCOILWTICO](https://fred.stlouisfed.org/series/DCOILWTICO), [DTWEXBGS](https://fred.stlouisfed.org/series/DTWEXBGS), [DGS10](https://fred.stlouisfed.org/series/DGS10); [ICE 지수 정의](https://www.ice.com/insights/indexation-and-etfs/managing-us-dollar-risk-in-uncertain_times).

Investing 조사 화면의 WTI는 `Dec 26`, 계약 크기 `1,000 Barrels`, USD/배럴 표시였다. 이 계약월을 영구 심볼로 고정하면 안 된다. 화면의 `Last Rollover Day`와 해당 계약월은 별개 필드이며, 이번 조사로 Investing의 전체 연결선물 조정 방식까지 확인한 것은 아니다. [WTI 원문](https://www.investing.com/commodities/crude-oil)

## KIS 공식 원천

### 마스터에서 확인한 식별자

KIS 공식 파서가 지정한 공개 ZIP을 TLS 검증을 유지한 `curl`로 받아 메모리에서 CP949로 읽었다. `frgn_code.mst`는 13,009행, ZIP SHA256은 `fb6e1c64e69c542cae8504da1b378352313de427879ded6ce53f28aa1a520cf2`였다. 파일 첫 문자 분류와 API의 `FID_COND_MRKT_DIV_CODE`는 **서로 다른 코드 체계**다. [공식 파서](https://github.com/koreainvestment/open-trading-api/blob/3a9be59a3ed8745720dc3aa9d584bdf3b7830362/stocks_info/overseas_index_code.py), [공식 구조체](https://github.com/koreainvestment/open-trading-api/blob/3a9be59a3ed8745720dc3aa9d584bdf3b7830362/stocks_info/해외주식지수정보.h), [마스터 원본](https://new.real.download.dws.co.kr/common/master/frgn_code.mst.zip)

| 마스터 분류 | 심볼 | 마스터 명칭 | API 후보 및 확인 수준 |
|---|---|---|---|
| `P` 미국지수 | `SPX` | `S&P 500 INDEX` / S&P500 | 일별 `N/SPX`. 상품 일치, 실제 응답 미검증 |
| `P` 미국지수 | `COMP` | `NASDAQ Composite` / 나스닥 종합 | 일별 `N/COMP`. 상품 일치, 실제 응답 미검증 |
| `P` 미국지수 | `NDX` | `NASDAQ-100 (DRM)` / 나스닥 100 | 종합과 다르므로 제외 |
| `C` 상품선물 | `WTIF` | `CRUDE OIL, LIGHT SWEET NYMEX` / WTI 근월 (USD) | 마스터 존재. 일별 API 시장코드·지원 여부 미확인 |
| `E` | `M0401` / `M0402` | WTI 뉴욕현물 / WTI 뉴욕근월물 | 다른 상품. `E`는 확인한 구조체 주석에도 설명 없음; API 지원 추정 금지 |
| `B` 주요국정부채 | `Y0202` | `US T-Note 10 Years(Y)` / 미국 10년T-NOTE 수익률 | 일별 `I/Y0202` 후보. 단위 스케일·채권 정의·갱신시각은 미확인 |

전체 마스터에서 `DXY`, `DOLLAR`, `달러지수`, `달러 인덱스` 등으로 검색했지만 정확한 DXY 식별자는 찾지 못했다. 환율 `FX@EUR` 등은 존재한다. 이는 조사한 마스터에서 찾지 못했다는 뜻이며 KIS 모든 상품에 DXY가 없다는 증명은 아니다. 환율들을 조합해 재구성한 값도 ICE의 공식 관측 DXY와 동일 출처로 표시할 수 없다.

### 기존 TR 재사용 범위

공식 `inquire_daily_chartprice.py`는 다음 계약을 문서화한다. [공식 조회 샘플](https://github.com/koreainvestment/open-trading-api/blob/3a9be59a3ed8745720dc3aa9d584bdf3b7830362/examples_llm/overseas_stock/inquire_daily_chartprice/inquire_daily_chartprice.py), [공식 응답 매핑](https://github.com/koreainvestment/open-trading-api/blob/3a9be59a3ed8745720dc3aa9d584bdf3b7830362/examples_llm/overseas_stock/inquire_daily_chartprice/chk_inquire_daily_chartprice.py)

- GET `/uapi/overseas-price/v1/quotations/inquire-daily-chartprice`, TR `FHKST03030100`. 샘플은 `real`·`demo` 모두 같은 TR을 사용한다. 계좌번호 파라미터는 없다.
- `FID_COND_MRKT_DIV_CODE`: `N` 해외지수, `X` 환율, `I` 국채, `S` 금선물. **`S`가 모든 상품선물을 지원한다고 확대 해석하면 안 된다.** `C`는 마스터 분류이므로 조회 파라미터로 바로 복사하지 않는다.
- `FID_INPUT_ISCD`, 시작/종료일 `YYYYMMDD`, `FID_PERIOD_DIV_CODE=D`를 사용한다.
- `output1`: `ovrs_nmix_prpr` 현재가, `ovrs_nmix_prdy_clpr` 전일 종가, `prdy_ctrt` 전일 대비율, `hts_kor_isnm`, `stck_shrn_iscd` 등.
- `output2[]`: `stck_bsop_date` 영업일자, `ovrs_nmix_prpr` 현재가, `ovrs_nmix_oprc/hgpr/lwpr` 등. 샘플은 이 값을 단순히 현재가라고 명명하며 완료된 일봉임을 보증하는 별도 플래그를 문서화하지 않는다.

현재 저장소 `kernel/broker/tr_ids.py`에 이 경로가 `OVS_FX_DAILY`로 등록돼 있다. 따라서 SPX/COMP를 위해 별도 인증·토큰 저장소를 만들 필요 없이 공통 KIS 클라이언트를 재사용할 수 있다. 기존 `get_fx_usdkrw()`의 `output1` 현재가와 `output2` 날짜 조합은 새 지표 파서의 시점 보증으로 재사용하지 않는다. [현재 TR 등록](../../src/trader/kernel/broker/tr_ids.py), [현재 시세 코드](../../src/trader/kernel/broker/market.py)

**시점 처리 권고:** 날짜와 값을 `output2`의 동일 행에서 취하고, 유효한 날짜를 파싱해 `asof` 이하 최신 행을 선택한다. 수신 순서를 최신순으로 가정하지 않는다. `observed_date`만 있는 응답은 `observed_at=null`, `timestamp_precision=day`, `session=daily_observation`로 보존한다. 수집 UTC 시각은 `received_at`에만 기록한다. 장중의 당일 행은 완료 종가로 단정하지 않는다. 과거 분석이면 해당 날짜 장 마감과 정보 공개 여부까지 고려하거나 전일 확정 관측을 선택한다. 단순 `date <= asof.date()`는 장중 미래값 방지 보장이 아니다.

미국채 `I/Y0202`는 시장 종류와 이름만 일치한다. 공식 샘플에서 `%` 스케일(예: `4.8`인지 `48`인지), timezone, 특정 벤치마크 채권/CMT 여부를 확정하지 못했으므로 응답·단위 검증 전에는 exact 사용처에 활성화하지 않는 것이 안전하다. 필요하면 원본 값을 `unit=unknown`으로 진단만 하고 LLM 수치 맥락에서는 제외한다. FRED `DGS10`은 문서상 단위가 percent로 확인된다. [DGS10 정의](https://fred.stlouisfed.org/series/DGS10)

분봉 TR `FHKST03030200`도 공식으로 존재하지만 문서 시장구분은 `N/X/KX`, 시간 구분은 `0` 정규장·`1` 시간외다. 이를 국채/WTI 지원 근거로 쓰지 않는다. [해외지수 분봉 샘플](https://github.com/koreainvestment/open-trading-api/blob/3a9be59a3ed8745720dc3aa9d584bdf3b7830362/examples_llm/overseas_stock/inquire_time_indexchartprice/inquire_time_indexchartprice.py)

### WTI를 정확한 월물로 확보할 후속 후보

KIS 해외선물 현재가 API는 `GET /uapi/overseas-futureoption/v1/quotations/inquire-price`, TR `HHDFC55010000`, 파라미터 `SRS_CD`를 공식 제공한다. 응답 매핑에는 `last_price`, `proc_date/proc_time`(최종처리일/시각), `expr_date`, `trd_to_date`, `sbsnsdate` 등이 있다. 이는 주문 API가 아닌 시세 API지만 이용 권한·지연·시각 timezone은 이번 인증 없는 조사로 확인하지 않았다. 처리시각을 마지막 체결시각으로 단정할 수도 없다. [공식 API](https://github.com/koreainvestment/open-trading-api/blob/3a9be59a3ed8745720dc3aa9d584bdf3b7830362/examples_llm/overseas_futureoption/inquire_price/inquire_price.py), [응답 매핑](https://github.com/koreainvestment/open-trading-api/blob/3a9be59a3ed8745720dc3aa9d584bdf3b7830362/examples_llm/overseas_futureoption/inquire_price/chk_inquire_price.py)

공식 `ffcode.mst`에서 `CLZ26`=`Crude Oil-202612`, 상품 `CL`, tick `0.01`, tick value `10`, 계약크기 `1000` 및 후속 월물들을 확인했다. 파서는 최근월물·최다월물 여부를 별도 필드로 정의한다. ZIP은 5,021행, SHA256 `88a33951cbfa14801f38c4088a22ef1b6597b44405e69a36e6e9b7e7c56ccc54`. 최근월물과 거래량이 많은 월물은 일반적으로 같은 개념이 아니므로 선택 규칙을 명시해야 한다. 계약번호를 함께 저장하면 현물보다 요청 WTI 선물에 가까운 정확한 상품을 제공할 수 있으나, Investing의 roll 시점·조정 방식까지 같다는 보장은 없다. [공식 선물 파서](https://github.com/koreainvestment/open-trading-api/blob/3a9be59a3ed8745720dc3aa9d584bdf3b7830362/stocks_info/overseas_future_code.py), [선물 마스터](https://new.real.download.dws.co.kr/common/master/ffcode.mst.zip), [CME WTI 상품](https://www.cmegroup.com/markets/energy/crude-oil/light-sweet-crude.contractSpecs.html)

이는 기존 지수 TR 두 항목 추가보다 큰 후속 통합이다. 월물 선택·만기·roll·계약별 시각·시세 권한을 검증하기 전에는 `DCOILWTICO` 현물을 명시적으로 표시하고 exact WTI coverage는 충족 처리하지 않는다.

## 공개 대안 및 접근 검증

| 원천 | 2026-11-07 확인 결과 | 안정적 무창 수집 판단 |
|---|---|---|
| Investing 5개 원문 | 검색/열람 도구로 상품명·시점 표시를 읽음. 로컬 기본 curl은 5/5 `403`, 3바이트 응답 | 실제 HTML/`__NEXT_DATA__`/JSON-LD 구조 검증 불가. 공식 공개 API 없음. 상시 수집 경로로 권고하지 않음 |
| Yahoo `query1.finance.yahoo.com/v8/finance/chart/` | `^IXIC`, `DX-Y.NYB`, `CL=F`, `range=5d&interval=1d` 각 1회 모두 `429`, `Edge: Too Many Requests` | 안정성·스키마·단위 검증 실패. 공개 제품 페이지도 열람 도구에서 429. 공식 지원 API나 SLA라고 주장하지 않음 |
| Stooq | 공개 `q/?s=...`는 JS 검증 페이지. `q/l/?s=dx.f|cl.f|^ndq&i=d` 각 1회 모두 HTTP 404 | 이 URL 결과만으로 서비스 전체 중단이라고 단정하지 않음. 공식 상품/연결선물 정의와 이용권한을 확인하지 못했으므로 채택 제외 |
| FRED | 기존 공식 observations API 구현 존재. 시리즈 정의·빈도·단위 문서 확인 | 현재 최소 변경의 일별 경로. 원본 상품명과 지연/관측일 표시 필요 |
| Treasury | 공식 안내에서 연결한 `yield.xml` 공개 HTTP 200, XML 파싱 성공, 최근 관측일 `2026-11-06` | 무인 일별 CMT/파 수익률 대안. 특정 채권 장중 yield의 exact 원천은 아님 |
| EIA NYMEX Futures | 공식 페이지가 2024-04-05 이후 선물 가격 미제공을 명시 | 현재 WTI 선물 대안으로 쓸 수 없음 |
| ICE / CME 직접 데이터 | DXY 공식 지수 및 CME REST/WebSocket·라이선스 경로 확인 | 정확한 장기 통합 후보지만 계정·상품권한·비용·용도 계약 확인 필요. 구매·키 생성은 수행하지 않음 |

근거: [Yahoo 약관](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html?ncid=mbr_idnedulnk00000001), [Stooq 페이지](https://stooq.com/q/?s=dx.f), [Treasury XML 안내](https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics/interest-rate-xml-files), [Treasury 방법론](https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics/treasury-yield-curve-methodology), [EIA 선물 중단 명시](https://www.eia.gov/dnav/pet/pet_pri_fut_s1_d.htm), [ICE DXY](https://www.ice.com/fixed-income-data-services/index-solutions/currency-indices), [CME 데이터 API·라이선스 안내](https://www.cmegroup.com/tools-information/webhelp/data-services-portal/Content/GettingStarted.html).

HTTP 결과는 이 실행 환경에서 받은 관측이며 공급업체 자체 차단인지 중간 네트워크 정책인지까지 판별한 것은 아니다. 인증·쿠키·브라우저·프록시·헤더 위장·재시도 폭증으로 해결하려 하지 않았다. 첫 Python urllib은 로컬 CA 오류를 냈고, TLS 검증을 끄지 않고 시스템 curl로 공개 파일을 읽었다.

Treasury는 최근 발행물의 대략 15:30 ET bid-side indicative quote에서 파 수익률 곡선을 산출하고, 통상 거래일 18:00 ET까지 공개하지만 지연될 수 있다고 설명한다. 수집시간과 산출 기준시각·게시시각은 세 가지 별개 개념이다. [Treasury 방법론](https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics/treasury-yield-curve-methodology)

## 이용조건과 데이터 전달

Investing 약관은 데이터의 이용·저장·전송 등에 사전 서면 허가가 필요하다고 명시하며, 거래소 직접값이 아닌 indicative/market-maker 값일 수 있다고 설명한다. Yahoo 약관도 자동 수집에 사전 허가를 요구한다. 화면 공개 여부만으로 무인 수집·외부 LLM 전송까지 허용됐다고 볼 근거는 없다. [Investing 약관](https://www.investing.com/about-us/terms-and-conditions), [Yahoo 약관](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html?ncid=mbr_idnedulnk00000001)

FRED의 `SP500`·`NASDAQCOM`에도 저작권 표시가 있다. 정부 데이터인 광의달러·CMT의 이용조건과 혼동하지 않는다. KIS 샘플 코드나 공개 종목 마스터의 접근 가능성 역시 시세 데이터 재배포·외부 모델 전송 권한을 증명하지 않는다. 여기서는 서비스 계약 판단을 대신하지 않으며, 외부 LLM 전달 범위는 이용 중인 데이터 계약 확인 항목으로 남긴다. [SP500 시리즈 약관](https://fred.stlouisfed.org/series/SP500), [NASDAQCOM 시리즈 약관](https://fred.stlouisfed.org/series/NASDAQCOM), [DTWEXBGS](https://fred.stlouisfed.org/series/DTWEXBGS), [DGS10](https://fred.stlouisfed.org/series/DGS10)

## 구현 권고와 검증 문턱

1. **지금:** `N/SPX`, `N/COMP`만 기존 KIS 공통 클라이언트에 작은 조회 메서드로 추가한다. `rt_cd` 성공·응답 스키마·이름/심볼·유효 숫자·날짜를 확인한다. 실제 시세 응답을 아직 확인하지 않았다면 coverage에 `live_verified=false`를 남긴다. 동일 상품의 FRED 일별값으로 실패를 격리한다.
2. **US10Y:** `I/Y0202` 단위·상품 정의·시각을 확인하기 전까지 FRED `DGS10`을 `10-year constant maturity yield, percent, daily`로 제공한다. `exact_investing_equivalent=false`를 유지한다.
3. **WTI/DXY:** `DCOILWTICO`와 `DTWEXBGS`를 각각 `WTI Cushing spot`·`Fed nominal broad USD index`라고 명명한다. 요청 지표 기준 `coverage=proxy` 또는 `exact_status=missing`; 저장 키가 과거 호환성 때문에 `wti/dxy`여도 의미 필드는 정확해야 한다. 원본과 대체값을 한 시계열에 잇거나 서로의 등락률을 계산하지 않는다.
4. **후속 WTI:** KIS `HHDFC55010000` + 선물 마스터의 월물로 별도 검증한다. 주문 권한과 무관하게 읽기 요청만 검증하며, 월물 심볼과 roll 정책을 보존한다. 정확 DXY는 ICE 또는 허가된 데이터 제공자의 지수 상품이 확인될 때 연결한다. DX 선물은 DXY 현물지수와 별개다.
5. **무창 운영:** 기존 OS 데몬에서 HTTP timeout·항목별 실패 격리·캐시·제한된 재시도·backoff를 사용한다. 브라우저 수집을 기본 의존성으로 추가하지 않는다. 소스 실패 때 마지막 정상 관측을 보존하되 기존 관측일과 `stale`을 유지하고 수신시각을 새 관측시각으로 덮어쓰지 않는다.

각 LLM 전달 항목의 권고 최소 필드: `requested_instrument`, `actual_instrument`, `source`, `series_id/symbol`, `unit`, `value`, `observed_date`, `observed_at`(없으면 null), `timestamp_precision`, `received_at`, `session`, `contract_month`(선물), `quality`(exact/proxy/missing), `stale`, `fallback_reason`, `source_url`, `live_verified`. 휴장일·다음날 게시·미국 DST를 고려하며 출처가 보증하지 않은 초 단위 시각을 생성하지 않는다.

검증은 SPX/COMP 식별자 혼동, 미정렬/빈/휴일 행, 실패 `rt_cd`, 수신시간과 관측일 혼합, 미래 일자, 장중 일봉을 확정 종가로 단정하는 경우, `%`/지수/배럴 단위 혼동, fallback의 proxy 표지 유실을 중심으로 한다. 위 항목은 조사에서 도출한 설계 권고이며 검증 완료 주장과 구분한다.

이번 구현의 확정 범위는 **KIS `N/SPX`·`N/COMP` 일별 우선 조회와 동일 상품 FRED fallback**이다. KIS US10Y·WTI 선물·exact DXY 연결은 추가 검증과 계약 확인이 필요한 후속으로 제외한다.

## 후속 확인: 일간 WTI와 시세 권한

2026-11-07 공식 일간 API도 확인했다. `GET /uapi/overseas-futureoption/v1/quotations/daily-ccnl`, TR `HHDFC55020100`이며 `SRS_CD`, `EXCH_CD`, `CLOSE_DATE_TIME`(YYYYMMDD), 최초 `QRY_TP=Q`, `QRY_CNT`(최대40)를 받는다. 시작시각·묶음·최초 이전조회키는 빈 값이다. 날짜와 가격은 `output2` 동일 행의 `data_date/last_price`로 연결해야 하며 정산가라고 표시하면 안 된다. 공식 샘플의 재귀는 조회구분과 이전조회키를 그대로 재사용하므로 그 연속조회 코드를 검증 없이 복사하지 않는다. [공식 일간 API](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/overseas_futureoption/daily_ccnl/daily_ccnl.py), [응답 필드](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/overseas_futureoption/daily_ccnl/chk_daily_ccnl.py)

03:40 UTC에 기존 프로그램의 인증 조립과 공유 토큰·조회 제한기를 사용하여 `CLZ26`, `EXCH_CD=CME`, 요청3행으로 GET 1회만 확인했다. 결과는 `KisApiError`, `msg_cd=HTTP500`으로 시세를 확보하지 못했다. 이 실패만으로 특정 권한 미가입이나 일시적 서버 장애를 단정하지 않는다. 주문·시세 신청·계약 변경은 하지 않았다. 따라서 exact WTI는 운영에 활성화하지 않으며 기존 FRED 현물 proxy를 유지한다.

KIS 공식 공지는 CME/CBOT/NYMEX/COMEX의 API 시세를 별도 신청 대상으로 설명한다. 현재 각 sub 거래소 월 USD 228.8, 2027-01-01부터 USD 236.5(VAT 포함)로 공지됐다. 무료 HTS/MTS와 API 이용 조건이 다르며, 이 일별 REST 조회만 사용할 때의 권한·예외 조건까지 공지로 확정되지는 않는다. 신규 구독을 자동 신청하지 않았다. 실제 시세 이용 계약, 지연 여부, `data_date`의 거래일/시간대, 월물 선택·roll 규칙 확인이 통합의 선행 조건이다. [KIS 공식 비용 안내](https://m.koreainvestment.com/main/customer/notice/Notice.jsp?cmd=TF04ga000002&num=47411)

ICE는 정확 DXY의 공식 공급 경로로 Data API·Data Files·Feed 등을 명시한다. 여섯 통화의 고정 가중치와 현물 호가로 계산하는 지수이며 DX 선물이나 연준 광의 달러지수와 다르다. 공개 카탈로그만으로 익명 자동수집 API나 현재 사용자의 계약 권한이 확보되는 것은 아니다. ICE 또는 허가된 공급자의 이용 권한·상품 식별자·시각 정의를 확보할 때 연결한다. [ICE 공식 Currency Indices 카탈로그](https://developer.ice.com/fixed-income-data-services/catalog/ice-data-indices-currency-indices)
