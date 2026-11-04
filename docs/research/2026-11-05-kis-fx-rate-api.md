# KIS Open API 로 USD/KRW 환율 얻기 — 조사 노트 (2026-11-05)

## 질문

한국투자증권 KIS Developers Open API(REST/WebSocket)에서 계좌 잔고 조회 없이, 가장 가볍게, 실전·모의 어느 쪽에서든 USD/KRW 환율 하나를 얻는 방법은 무엇인가.

조사 범위: 공식 샘플 저장소 `koreainvestment/open-trading-api`(main, 커밋 `3a9be59`, 2026-10-28)의 `examples_llm/`, `examples_user/`, `legacy/`(Postman 컬렉션·README), `MCP/Kis Trading MCP/configs/overseas_stock.json`, 그리고 KIS 공식 마스터 파일 `frgn_code.mst`. apiportal(https://apiportal.koreainvestment.com) 의 API 상세 페이지는 SPA 라 WebFetch 로 본문(TR_ID·필드 표)을 읽지 못했다. 그래서 apiportal 기준 확인은 전부 **미확인**이고, 아래 근거는 KIS 가 직접 올린 공식 샘플과 마스터 파일이다.

## 요약 결론·추천

- **계좌가 필요 없는 환율 전용 경로가 있다.** `해외주식 종목_지수_환율기간별시세(일_주_월_년)[v1_해외주식-012]`, TR `FHKST03030100`, `GET /uapi/overseas-price/v1/quotations/inquire-daily-chartprice` 에 `FID_COND_MRKT_DIV_CODE=X`(환율), `FID_INPUT_ISCD=FX@KRW`(원/달러)를 넣는다. 공식 샘플 기준 실전·모의 TR id 가 같고(`FHKST03030100`), 모의 Postman 컬렉션에도 VTS 도메인으로 들어 있다.
- 값은 `output1.ovrs_nmix_prpr`(현재가)이고, 일자는 `output2[0].stck_bsop_date` 다. 오늘 하루치만 받으려면 `FID_INPUT_DATE_1=FID_INPUT_DATE_2=오늘`, `FID_PERIOD_DIV_CODE=D` 로 부른다.
- 1차 대안(계좌 불필요, 실전): `해외주식 현재가상세` `HHDFS76200200` 의 `output.t_rate`(당일환율)·`p_rate`(전일환율). 이 값은 종목 시세에 딸려 오는 환율이라 아무 미국 종목(예: `EXCD=NAS&SYMB=AAPL`)이나 넣고 부른다.
- 계좌 범위 대안(최후): `해외주식 매수가능금액조회` `TTTS3007R`/`VTTS3007R` 의 `output.exrt`(환율) → `해외주식 체결기준현재잔고` `CTRP6504R`/`VTRP6504R` 의 `bass_exrt`(기준환율)·`frst_bltn_exrt`(최초고시환율).
- 추천 순서: `FHKST03030100`(X / `FX@KRW`) → `HHDFS76200200.t_rate` → 기존 FRED 폴백. 계좌 범위 TR 은 `fx.kis_fx` 에 넣지 않는다(시세 경로가 계좌를 알게 됨).
- 환율 실시간 WebSocket TR 은 공식 샘플·목록에서 찾지 못했다(**없음으로 판단, 포털 원문은 미확인**).

## 후보별 확인 표

| # | API (공식 이름) | TR id 실전 / 모의 | Method + Path | 계좌 필요 | 모의 | 환율 필드 | 출처 |
|---|---|---|---|---|---|---|---|
| A | 해외주식 종목_지수_환율기간별시세(일_주_월_년) [v1_해외주식-012] | `FHKST03030100` / `FHKST03030100` | GET `/uapi/overseas-price/v1/quotations/inquire-daily-chartprice` | 아니오 | 지원(샘플·모의 Postman) | `output1.ovrs_nmix_prpr`(현재가), `output2[].ovrs_nmix_prpr`(일별 종가) | [S1][S2][S7] |
| B | 해외지수분봉조회 [v1_해외주식-031] | `FHKST03030200` / 없음 | GET `/uapi/overseas-price/v1/quotations/inquire-time-indexchartprice` | 아니오 | 미지원(legacy 표: 실전만) | `output1.ovrs_nmix_prpr`, `output2[].optn_prpr`(분봉, 샘플 매핑 기준) | [S3][S6] |
| C | 해외주식 현재가상세 [v1_해외주식-029] | `HHDFS76200200` / (샘플에 모의 분기 없음) | GET `/uapi/overseas-price/v1/quotations/price-detail` | 아니오 | 미지원(legacy 표). 단 우리 `tr_ids.py` 는 모의도 같은 TR 로 적어 둠 → **미확인** | `output.t_rate`(당일환율), `output.p_rate`(전일환율) | [S4][S6] |
| D | 해외주식 현재체결가 [v1_해외주식-009] | `HHDFS00000300` / `HHDFS00000300` | GET `/uapi/overseas-price/v1/quotations/price` | 아니오 | 지원 | **없음**(응답: rsym, zdiv, base, pvol, last, sign, diff, rate, tvol, tamt, ordy). `rate` 는 등락율 | [S5] |
| E | 해외주식 매수가능금액조회 [v1_해외주식-014] | `TTTS3007R` / `VTTS3007R` | GET `/uapi/overseas-stock/v1/trading/inquire-psamount` | **예**(`CANO`,`ACNT_PRDT_CD` + `OVRS_EXCG_CD`,`OVRS_ORD_UNPR`,`ITEM_CD`) | 샘플은 `VTTS3007R` 로 지원, legacy 표는 실전만 → 충돌, **미확인** | `output.exrt`(환율) | [S8][S6] |
| F | 해외주식 체결기준현재잔고 [v1_해외주식-008] | `CTRP6504R` / `VTRP6504R` | GET `/uapi/overseas-stock/v1/trading/inquire-present-balance` | **예**(`CANO`,`ACNT_PRDT_CD`,`WCRC_FRCR_DVSN_CD`,`NATN_CD`,`TR_MKET_CD`,`INQR_DVSN_CD`) | 지원 | `bass_exrt`(기준환율), `frst_bltn_exrt`(최초고시환율) | [S9][S6] |
| G | 해외증거금 통화별조회 [해외주식-035] | `TTTC2101R` / 없음 | GET `/uapi/overseas-stock/v1/trading/foreign-margin` | **예**(`CANO`,`ACNT_PRDT_CD`) | 미지원(샘플에 모의 TR 없음, legacy 표 실전만) | `output[].bass_exrt`(기준환율, 통화·국가별 행) | [S10][S6] |
| H | 해외주식 결제기준잔고 [해외주식-064] | `CTRP6010R` / 없음 | GET `/uapi/overseas-stock/v1/trading/inquire-paymt-stdr-balance` | **예** | 미지원 | `bass_exrt`, `frst_bltn_exrt` | [S11] |
| I | 주식통합증거금 현황(국내) | `TTTC0869R` / 없음 | GET `/uapi/domestic-stock/v1/trading/intgr-margin` | **예** | 미지원 | `usd_frst_bltn_exrt`(미국달러최초고시환율) 외 HKD/JPY/CNY | [S12] |
| J | 환율 실시간 WebSocket | — | — | — | — | 공식 실시간 목록의 해외주식 항목은 실시간호가·지연호가(아시아)·실시간지연체결가·실시간체결통보뿐. 환율 TR 없음 | [S6] |

HTTP 메서드는 공식 샘플의 `ka._url_fetch(...)` 기본 호출(조회 TR 은 GET)을 따른 것이다. apiportal 원문의 Method 칸은 **미확인**이다.

## 각 후보 상세

### A. `FHKST03030100` 종목_지수_환율기간별시세 — 추천

- 공식 샘플 `examples_llm/overseas_stock/inquire_daily_chartprice/inquire_daily_chartprice.py` 에서 `env_dv` 가 `real` 이든 `demo` 든 `tr_id = "FHKST03030100"` 을 쓴다(주석 "모의투자 지원 로직") [S1].
- 요청 파라미터(전부 필수): `FID_COND_MRKT_DIV_CODE`(`N` 해외지수, `X` 환율, `I` 국채, `S` 금선물), `FID_INPUT_ISCD`, `FID_INPUT_DATE_1`·`FID_INPUT_DATE_2`(YYYYMMDD), `FID_PERIOD_DIV_CODE`(`D`/`W`/`M`/`Y`). 계좌 파라미터는 없다 [S1].
- 종목코드는 "해외주식 마스터 코드 참조 (포럼 > FAQ > 종목정보 다운로드(해외) > 해외지수)" 라고 되어 있다. 이 마스터 파일이 `frgn_code.mst` 이고, 공식 파서 `stocks_info/overseas_index_code.py` 가 `https://new.real.download.dws.co.kr/common/master/frgn_code.mst.zip` 에서 받는다 [S13]. 레코드 정의 `stocks_info/해외주식지수정보.h` 의 `cls_code` 에서 `X` 가 환율이다 [S14].
- 2026-11-05 에 받은 `frgn_code.mst` 의 `X` 구분 행 중 원화 관련 행:
  - `FX@KRW` — 대한민국, `원/달러(KMB)`, 업종코드 `SMBS100`
  - `FX@KRWKFTC` — 대한민국, `원/달러`, `SMBS100`
  - `FX@KRWJS` — 대한민국, `원/엔`
  - 그 밖에 `FX@JPY`, `FX@EUR`, `FX@CNY` 등 달러 기준 크로스 환율이 있다.
  - `FX@KRW` 와 `FX@KRWKFTC` 의 차이(KMB = 서울외국환중개 매매기준율인지, KFTC = 한국자금중개인지)는 **미확인**이다. 원문에 표기는 있지만 정의가 없다. 실기 1회 호출로 두 값을 비교해 골라야 한다.
- 응답 필드(공식 샘플 `chk_` 의 매핑) [S2]:
  - `output1`(단일): `ovrs_nmix_prpr`(현재가), `ovrs_nmix_prdy_vrss`(전일 대비), `prdy_vrss_sign`, `prdy_ctrt`(전일 대비율), `ovrs_nmix_prdy_clpr`(전일 종가), `hts_kor_isnm`, `stck_shrn_iscd`, `ovrs_prod_oprc`/`hgpr`/`lwpr`
  - `output2`(배열): `stck_bsop_date`(영업 일자), `ovrs_nmix_prpr`(해당일 종가/현재가), `ovrs_nmix_oprc`/`hgpr`/`lwpr`, `acml_vol`, `mod_yn`
  - 형식: 문자열 숫자(KIS 공통). 원/달러 값의 소수 자릿수(예: `1385.50`)는 **미확인**이다. 실기 응답으로 확인해야 한다.
  - `output1.ovrs_nmix_prpr` 가 장중 현재값인지 최근 고시값인지(갱신 주기)는 **미확인**이다.
- 모의: 모의 Postman 컬렉션 `legacy/postman/모의계좌_POSTMAN_샘플코드_v1.6.json` 의 "V_해외주식 종목/지수/환율기간별시세" 가 `{{VTS}}/uapi/overseas-price/v1/quotations/inquire-daily-chartprice` 와 `tr_id: FHKST03030100`("[실전투자/모의투자]")를 쓴다 [S7]. `legacy/postman/README.md` 표에서도 모의·실전이 모두 ⭕ 이다 [S6].
- 연속조회: 샘플은 응답 헤더 `tr_cont` 가 `M`/`F` 면 다음 페이지를 부른다. 같은 날짜 하루만 조회하면 한 번으로 끝난다. 하루 조회가 빈 `output2` 를 주는지(휴장일·장전)는 **미확인**이다. 안전하게 가려면 최근 7일 범위로 부르고 `output2[0]`(최신일)을 쓴다.
- 요청 예: `GET /uapi/overseas-price/v1/quotations/inquire-daily-chartprice?FID_COND_MRKT_DIV_CODE=X&FID_INPUT_ISCD=FX@KRW&FID_INPUT_DATE_1=20261029&FID_INPUT_DATE_2=20261105&FID_PERIOD_DIV_CODE=D` (헤더 `tr_id: FHKST03030100`, `custtype: P`, 토큰·appkey·appsecret)

### B. `FHKST03030200` 해외지수분봉조회

- `FID_COND_MRKT_DIV_CODE` 설명이 "N 해외지수 X 환율 KX 원화환율" 이다. 별도의 `KX`(원화환율) 구분이 있다는 뜻이다 [S3]. 실전 Postman 컬렉션 설명도 "시장분류코드(N:해외지수 X:환율 KX:원화환율)" 이다 [S7].
- 필수 파라미터: `FID_COND_MRKT_DIV_CODE`, `FID_INPUT_ISCD`, `FID_HOUR_CLS_CODE`(0 정규장/1 시간외), `FID_PW_DATA_INCU_YN`(Y/N). 계좌는 필요 없다.
- `KX` 일 때 넣을 종목코드는 **미확인**이다(`FX@KRW` 를 그대로 쓰는지 등). 모의는 미지원이다(legacy 표에서 모의 칸이 비어 있음, 샘플에 모의 분기 없음) [S6].
- 장중 분 단위 값이 필요할 때만 의미가 있다. 하루 캐시인 `fx.rate` 에는 과하다.

### C. `HHDFS76200200` 해외주식 현재가상세

- 파라미터: `AUTH`(빈 문자열), `EXCD`, `SYMB`. 계좌는 필요 없다 [S4].
- 응답 `output` 의 환율 필드(샘플 매핑): `t_rate`(당일환율), `p_rate`(전일환율). 이와 함께 `t_xprc`(원환산당일가격), `p_xprc`(원환산전일가격) 등 원환산 필드가 있다 [S4].
- 종목 환율이라 `EXCD=NAS` 같은 미국 종목이면 USD/KRW 로 볼 수 있다. 단 어떤 고시환율인지(최초고시/매매기준 등)는 **미확인**이다.
- 모의: 샘플에 `env_dv` 가 없고 legacy 표에서 모의 칸이 비어 있다 → 모의 미지원으로 본다 [S6]. 우리 저장소 `tr_ids.py` 의 `OVS_PRICE_DETAIL` 은 모의도 같은 TR 로 적어 두었지만, `KisMarket` 이 실전 env 만 허용하므로 실제 영향은 없다.
- 이미 `tr_ids.py` 에 `OVS_PRICE_DETAIL` 로 등록돼 있다. 섹터 제안에서 쓰는 TR 이므로 추가 등록 없이 재사용할 수 있다.

### D. `HHDFS00000300` 해외주식 현재체결가

- 응답 필드에 환율이 없다(`rate` 는 등락율) [S5]. → **반증**. 참고로 질문에 적힌 "`HHDFS76200200` 현재체결가" 는 이름이 틀렸다. `HHDFS76200200` 은 현재가상세(C)이고, 현재체결가는 `HHDFS00000300` 이다.

### E. `TTTS3007R` 해외주식 매수가능금액조회

- 공식 샘플: 실전 `TTTS3007R`, 모의 `VTTS3007R`. 필수 파라미터는 `CANO`, `ACNT_PRDT_CD`, `OVRS_EXCG_CD`(NASD 등), `OVRS_ORD_UNPR`(해외주문단가), `ITEM_CD`(종목코드) [S8].
- 응답 `output.exrt`(샘플 매핑: '환율'). 적용환율인지 고시환율인지 원문 정의는 **미확인**이다.
- 모의 지원 여부가 충돌한다. 신규 `examples_llm` 은 `VTTS3007R` 을 제공하지만, `legacy/README.md`·`legacy/postman/README.md` 표에서는 모의 칸이 비어 있다 [S6][S8]. 최신 샘플 쪽이 맞을 가능성이 높지만 **미확인**이다.
- 계좌 1개와 종목·단가 입력이 필요하다. 계좌 범위 중에서는 응답이 가장 작은 호출이다.

### F. `CTRP6504R` 해외주식 체결기준현재잔고

- 실전 `CTRP6504R` / 모의 `VTRP6504R` 이다. "실전 전용?" 이라는 질문은 **반증**된다 [S9].
- 환율 필드: `bass_exrt`(기준환율), `frst_bltn_exrt`(최초고시환율). 샘플은 output1~3 을 같은 매핑으로 rename 해서, 필드별 소속 블록은 샘플만으로 **미확인**이다. legacy 주석에는 "외화평가금액은 당일 최초고시환율이 적용된 금액" 이라고 되어 있다 [S15].
- 잔고 전체를 받으므로 무겁다. 우리 저장소 `tr_ids.py` 에 `OVS_PRESENT_BALANCE` 로 이미 있다.

### G. `TTTC2101R` 해외증거금 통화별조회

- TR id 는 `TTTC2101R`(실전 전용, 샘플에 모의 분기 없음)이다. 파라미터는 `CANO`, `ACNT_PRDT_CD` 뿐이다 [S10].
- 응답 `output[]` 이 통화별 행이고, `bass_exrt`(기준환율)가 있다. 계좌가 필요하고 모의는 미지원이다.

### H·I. 결제기준잔고(`CTRP6010R`)·국내 통합증거금(`TTTC0869R`)

- 둘 다 계좌가 필요하고 실전 전용이다. `bass_exrt`·`frst_bltn_exrt`, `usd_frst_bltn_exrt` 가 있다 [S11][S12]. 환율만을 위해 쓸 이유는 없다.

### J. 환율 전용 API·실시간

- 공식 샘플·Postman 목록에서 이름에 '환율'이 들어간 API 는 A(종목_지수_환율기간별시세)와, B 의 시장구분 `X`/`KX` 뿐이다. "기준환율"·"외환"·"통화코드 조회" 같은 단독 REST API 는 찾지 못했다(apiportal 전체 목록과 대조하지 못했으므로 **미확인**).
- WebSocket: 공식 실시간 목록(해외주식 실시간호가·지연호가(아시아)·실시간지연체결가·실시간체결통보, 해외선물옵션 실시간체결가·호가 등)에 환율 실시간 TR 이 없다 [S6]. 국내 상품선물(미국달러선물) 실시간 체결로 근사할 수는 있지만 현물 환율이 아니고 이번 조사 범위 밖이다.

### 레이트 리밋

- apiportal 원문의 TR 별 제한은 **미확인**이다. 우리 저장소 `kernel/broker/client.py` 는 앱키당 실전 20건/초, 모의 2건/초(`RATE_PER_SEC`)로 제한하고, 공식 샘플 `kis_auth.smart_sleep()` 도 연속 호출 사이에 쉰다. `fx.rate` 는 KST 하루 1회 캐시이므로 제한은 문제가 되지 않는다.

## `fx.kis_fx` 연결 메모

- 주입점: `src/trader/features/quotes/fx.py` 의 `kis_fx: Callable[[], dict | None] | None`. `_try` 는 `rate`(str/Decimal 로 바뀌는 값)와 `as_of` 만 읽고, `pair`·`source` 는 `rate()` 가 채운다. 따라서 콜러블은 `{"rate": ..., "as_of": "YYYY-MM-DD"}` 만 돌려주면 된다(현재 docstring 과 같음).
- TR 등록: `src/trader/kernel/broker/tr_ids.py` 의 `_TR` 에 다음 한 줄을 더한다. `"OVS_FX_DAILY": ("/uapi/overseas-price/v1/quotations/inquire-daily-chartprice", "FHKST03030100", "FHKST03030100")`. 모듈 docstring 대로 "공식 examples_llm 에서 확인" 날짜를 적는다.
- 메서드: `src/trader/kernel/broker/market.py` 의 `KisMarket` 에 `get_fx_usdkrw()`(또는 `get_fx(code="FX@KRW")`)를 더한다. 패턴은 `get_index_kr` 와 같게 `self._client.get(path("OVS_FX_DAILY"), "OVS_FX_DAILY", {"FID_COND_MRKT_DIV_CODE": "X", "FID_INPUT_ISCD": "FX@KRW", "FID_INPUT_DATE_1": d1, "FID_INPUT_DATE_2": d2, "FID_PERIOD_DIV_CODE": "D"})` 로 부른다. 파싱은 `broker._parse` 의 `dec`·`rows` 를 쓴다. `rate = dec(output1.ovrs_nmix_prpr)` 이고, 0 이면 `rows(output2)[0].ovrs_nmix_prpr` 로 대신한다. `as_of = output2[0].stck_bsop_date` 를 `YYYY-MM-DD` 로 바꿔 쓴다. 날짜 범위는 `self._now()` 를 `config.KST` 로 바꿔 최근 7일로 잡는다.
- 클라이언트 조립: `KisMarket` 은 실전 env 만 허용한다(생성자 검사). `features/quotes/routes.py::_build_runtime` 이 이미 `load_secrets().kis_creds(env="real")` → `factory.build_market(creds)` 로 런타임 `market` 을 만든다. 같은 런타임에서 `fx.kis_fx = market.get_fx_usdkrw` 처럼 주입하면 클라이언트·토큰·레이트리미터(`factory.client_for` 가 앱키별로 공유)를 재사용한다. A 는 모의도 지원하지만, 이 프로젝트는 시세를 실전 env 로만 부르므로 모의 지원 여부는 실익이 없다.
- 폴백: A 가 실패하거나 0 을 주면 `OVS_PRICE_DETAIL`(이미 등록됨)의 `output.t_rate` 를 쓴다. 미국 대형주 하나(예: `NAS`/`AAPL`)로 부른다. 그다음은 기존 `macro_usdkrw`(FRED)다. 이 체인은 `kis_fx` 콜러블 안에서 처리하거나, `_try` 체인에 원천을 하나 더 두는 식으로 구현한다.
- 실기 확인이 필요한 항목(사용자 요청): `FX@KRW` 와 `FX@KRWKFTC` 의 값 차이, `ovrs_nmix_prpr` 의 자릿수·갱신 주기, 하루 범위 조회의 빈 응답 여부. 테스트는 네트워크를 막고 합성 응답 fixture 로 한다.

## 출처 목록

- [S1] https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/overseas_stock/inquire_daily_chartprice/inquire_daily_chartprice.py
- [S2] https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/overseas_stock/inquire_daily_chartprice/chk_inquire_daily_chartprice.py
- [S3] https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/overseas_stock/inquire_time_indexchartprice/inquire_time_indexchartprice.py
- [S4] https://github.com/koreainvestment/open-trading-api/tree/main/examples_llm/overseas_stock/price_detail (`price_detail.py`, `chk_price_detail.py`)
- [S5] https://github.com/koreainvestment/open-trading-api/tree/main/examples_llm/overseas_stock/price (`price.py`, `chk_price.py`)
- [S6] https://github.com/koreainvestment/open-trading-api/blob/main/legacy/postman/README.md (모의/실전 제공 여부 표), https://github.com/koreainvestment/open-trading-api/blob/main/legacy/README.md (API 제공 목록·실시간 목록)
- [S7] https://github.com/koreainvestment/open-trading-api/tree/main/legacy/postman (`모의계좌_POSTMAN_샘플코드_v1.6.json`, `실전계좌_POSTMAN_샘플코드_v2.6.json`)
- [S8] https://github.com/koreainvestment/open-trading-api/tree/main/examples_llm/overseas_stock/inquire_psamount
- [S9] https://github.com/koreainvestment/open-trading-api/tree/main/examples_llm/overseas_stock/inquire_present_balance
- [S10] https://github.com/koreainvestment/open-trading-api/tree/main/examples_llm/overseas_stock/foreign_margin
- [S11] https://github.com/koreainvestment/open-trading-api/tree/main/examples_llm/overseas_stock/inquire_paymt_stdr_balance
- [S12] https://github.com/koreainvestment/open-trading-api/tree/main/examples_llm/domestic_stock/intgr_margin
- [S13] https://github.com/koreainvestment/open-trading-api/blob/main/stocks_info/overseas_index_code.py, 마스터 파일 https://new.real.download.dws.co.kr/common/master/frgn_code.mst.zip (2026-11-05 수신)
- [S14] https://github.com/koreainvestment/open-trading-api/blob/main/stocks_info/해외주식지수정보.h
- [S15] https://github.com/koreainvestment/open-trading-api/blob/main/legacy/Sample01/kis_ovrseastk.py (`FHKST03030100`/`FHKST03030200` 호출부와 잔고 주석)
- 보조: https://github.com/koreainvestment/open-trading-api/blob/main/MCP/Kis%20Trading%20MCP/configs/overseas_stock.json (API 이름·경로·파라미터 설명 교차 확인)
- 미열람: https://apiportal.koreainvestment.com (SPA 라 본문을 읽지 못함 — 포털 기준 사항은 미확인)
