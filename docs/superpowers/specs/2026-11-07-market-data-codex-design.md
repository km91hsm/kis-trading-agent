# KIS 시장 자료와 Codex 매매 토론 설계

## 사용자 요청과 현재 근거

사용자는 거래대금·가격·등락률을 KIS에서 수집하는 방향을 승인했고, Claude와 Codex가 매매 토론에 함께 참여하도록 설정 ON/OFF를 요청했다. 뉴스·거래대금 순위·거래대금 변화율·매크로 지수의 실제 LLM 전달 여부도 확인한다. 이 문서의 세부 구현 선택은 에이전트의 판단이다.

기준은 origin/main `183bcb341f611b6a0330cb9da1d9eb53534d226e`다. 실행 중인 main/매매 daemon과 기존 SaveTicker Native 번들은 별도로 유지하며 작업은 `.worktrees/kis-data-codex-debate`에서 한다. 과거 1시간 시험에서 SaveTicker 20개 중 실시간 거래대금만 저장되지 않았다. 현재 KIS quote는 연결돼 있지만 해외 거래대금 순위 어댑터는 없다.

감사에서 자동 종목 `InputsPortImpl.collect`의 `saveticker_cfg` 주입 누락, 최신순 뉴스의 앞부분을 버리는 `shrink`, 압축 후 byte 상한 초과, freshness 정보 유실을 확인했다. 현재 SaveTicker 시세 요약은 regular만 사용하며 거래대금 변화율 계산은 없다. 뉴스/매크로/자료 요약은 역할별 제한을 거치므로 '모든 원문을 모든 모델이 읽는다'는 설명은 하지 않는다.

## 접근 선택

1. SaveTicker 복구만 강화하는 방법은 시세의 브라우저 의존을 남긴다. 선택한 방법은 기존 quotes 기능에 KIS 수집·저장·읽기를 추가하고 뉴스·리포트·커뮤니티 자료는 SaveTicker를 계속 사용한다. KIS를 SaveTicker 성공 기록으로 위장하지 않는다.
2. Codex를 제3의 토론자로 추가하면 전체 역할/요약 스키마가 커진다. 기존 bull/bear 역할에 공급자를 배정한다. OFF는 기존 Claude/Claude, ON은 Claude bull/Codex bear다. 기존 리스크·주문 엔진을 유지한다.
3. 새로운 API key·API 과금 연동 대신 설치된 Codex CLI의 ChatGPT 로그인을 이용한다. 현재 CLI 0.163.0과 로그인 상태를 확인했다. 모델 호출 없이 재현 가능한 테스트를 먼저 한다.

## KIS 수집과 저장

- 기존 broker 인증·공유 limiter를 사용한다. 새로운 브라우저나 계좌를 만들지 않는다.
- 미국 `trade-pbmn`/`HHDFS76320010`, `updown-rate`/`HHDFS76290000`를 NAS/NYS/AMS별로 조회한다. `NDAY=0`, 상승 `GUBN=1`, 하락 `GUBN=0`, `VOL_RANG=0`. 국내는 `volume-rank`의 `FID_BLNG_CLS_CODE=3`과 등락률 순위를 이용한다.
- 새 broker DTO는 ticker, exchange, currency, source_rank, price, change_pct(소수 비율), volume, turnover(Decimal 또는 None)를 구분한다. 누락을 0으로 바꾸지 않는다. 수집 시각은 원천 시세 시각이 아니다.
- quotes에 30초 주기 데몬 잡, 제한된 페이지 조회, SQLite append-only snapshot, GET 저장 자료 조회를 추가한다. 각 거래소/종류 실패를 격리하고 이전 성공 자료와 최신 실패를 함께 표시한다. quote/주문의 공유 limiter를 사용하고 페이지·후보 수를 제한한다.
- 마이그레이션16은 quotes 소유, 17은 Codex 세션 소유로 예약한다. 기존 마이그레이션을 수정하지 않는다. 신규 표는 과거 asof에서 `received_at <= asof`를 강제한다.
- 공개 계약: `quotes.api.activity_snapshot(conn, market, *, asof, max_age_s=120) -> dict`, `activity_for_ticker(snapshot, ticker) -> dict`. snapshot은 source, market, currency, received_at, source_asof, basis, status, stale, coverage, rankings(turnover/gain/loss), errors를 가진다. 데이터 없음도 명시적인 dict다. 포트 이름은 `market_activity`, `snapshot(conn, market, *, asof)`를 제공한다.
- 순위는 수집된 거래소·페이지 범위를 표시한다. 미검증 거래소 전체성/세션 범위는 partial/unknown이다. 국내와 미국, KRW와 USD를 합쳐 정렬하지 않는다. 가격·등락률은 KIS 원천 값을 사용한다.
- 거래대금 변화율은 실제 누적 거래대금 관측으로만 계산한다. 최근5분액과 직전5분액의 비교는 동일 종목·거래소·통화·운영일·관측 세션의 세 시점이 모두 있고 각 경계 오차60초 이내일 때만 계산한다. 초기화/음수 차분/분모0/누락은 unavailable. 원천 세션 기준이 미확인임을 보존하고 'API 누적액의 관측 변화'로 명시한다. `현재가×거래량`이나 거래량증가 API를 거래대금 증가율로 쓰지 않는다.
- 장마감의 같은 값을 새로 조회했다고 실시간 체결로 표시하지 않는다. 30초는 polling 주기이며 원천 지연 보장은 아니다.

## LLM 입력 계약

- ingest 포트에서 사용자 지정 SaveTicker 설정을 주입한다. 입력의 시장을 전달해 미국 순위가 국내 후보 순위로 섞이지 않게 한다. 미국 지수의 글로벌 참고가 필요하면 명시적 US/global 범위로 표시한다.
- `market_activity`를 종목00_inputs, 시장 분석, scout에 연결한다. 분석가와 토론자에게 compact context를 주며 원본 전체 전달로 오해시키지 않는다. 기존 뉴스/매크로/리포트/캘린더와 각각의 source/asof/status/stale/data_gaps를 보존한다.
- SaveTicker reader와 scout에서 freshness/부분수집 정보를 버리지 않는다. '자료 없음'을 '순위 미진입'으로 단정하지 않는다.
- `shrink`는 최신순 뉴스의 앞부분과 오름차순 시계열의 뒷부분을 보존하며 UTF-8 24,000 bytes 상한을 실제로 지킨다. 축약 기록과 핵심 품질/출처 정보를 남긴다.
- 거래대금 관련 전달 경로는 기존 SaveTicker regular 값을 KIS 성공처럼 덮어쓰지 않고 `market_activity`로 분리한다. 수집 실패·미준비인 값은 모델이 확인할 data gap이다.
- 사용자 추가 요청: S&P500·나스닥종합·WTI·달러지수·미국채10년물과 `ranto28` 블로그의 실제 전달을 항목별 `source_coverage`로 기록한다. 기존 FRED `SP500`, `NASDAQCOM`, `DCOILWTICO`, `DTWEXBGS`, `DGS10`을 구분하며 Investing.com 직접 수집으로 표시하지 않는다. WTI 현물과 선물, 연준 광의달러와 ICE DXY는 동등한 지표로 표시하지 않는다. 블로그 RSS의 발행·수신시각과 발췌를 보존한다.
- 정확한 지표와 안정적인 수집원을 우선한다는 추천에 따라 S&P500·나스닥종합은 공식 KIS `N/SPX`, `N/COMP` 일간 관측을 우선하고 실패 시 FRED로 대체한다. `output2`의 동일 행 값과 일자를 짝지으며 `output1` 현재값에 과거 일자를 붙이지 않는다. asof의 미국 시장 날짜 전일까지 사용하고 일간 참고임을 밝힌다. `macro.kis_indices`로 끌 수 있다. DXY·WTI 선물의 자동 원천과 KIS 국채 단위·선물 계약 롤은 검증되지 않았으므로 추가하지 않으며 기존 자료의 상품 차이와 지연을 명시한다.
- 공개 `InputsPort.research_context`를 통해 FO·시장·scout에도 매크로/블로그/거래대금/coverage를 전달한다. 블로그 언어가 한국어라는 이유로 US 분석에서 제외하지 않는다. FO의 Section 뉴스 구조를 풀어서 compact context에 포함한다.

## Codex 토론 계약

- `llm.codex_debate`: enabled(default false), model(optional/default CLI), timeout_s, daily_call_cap, bin(optional). 웹 control override로 ON/OFF 저장. 변경은 다음 새 연구 세션부터 적용한다.
- ON의 spot/시장/FO 새 연구는 bull=Claude, bear=Codex, 반박 횟수 `max(1, debate_rounds)`. 라운드0은 독립 의견, 이후 각자 상대 이전 턴을 보고 반박한다. 합의에 의한 토론 생략은 ON에서 금지한다. OFF 기존 경로·횟수는 유지한다.
- 양쪽이 각각 독립 쟁점과 근거를 먼저 제시한다. 쟁점 ID를 이용해 상대 쟁점에 대한 반론 누락을 검증하며 이후 라운드에 재답변한다. 달러·유가·금리 상승을 일방적 악재로 고정하지 않고 성장·인플레·공급 충격 및 업종별 효과를 검토한다.
- Codex는 JSON 입력과 DebateTurn schema만 받는다. 부모 Python이 검증·저장하며 모델이 주문/브로커/계좌/프로그램 경로를 받지 않는다. scope/market/asof/ticker/currency를 명시한다.
- CLI는 임시 cwd, 최소 env, `--ignore-user-config --ephemeral --sandbox read-only --output-schema --json --output-last-message`를 사용한다. shell, MCP, apps, browser/computer, multi-agent, hooks, web 등 토론에 불필요한 도구를 child에서 차단한다. 권한 우회 플래그와 `--ignore-rules`는 사용하지 않는다. 인증정보를 복사하거나 출력하지 않는다.
- 세션 정책을 시작 시 고정한다. provider/model/입력·프롬프트·스키마 해시가 다른 기존 턴을 Codex 참여로 재사용하지 않는다. 일반 checkpoint_write로 Codex 증명을 위조할 수 없다.
- 기존 결정/주문/체크포인트를 자동 삭제하지 않는다. 이미 완료된 분석·기존 결정 재개는 원래 정책으로 보존한다. 새 정책과 충돌하는 미완료 연구는 명확한 재분석 필요 오류 또는 보존된 연구 revision으로 처리한다.
- Codex 미설치/미로그인/시간초과/비정상 종료/JSON·스키마 오류/상한 소진은 오류로 기록하며 Claude로 조용히 대체하지 않는다. 필수 Codex 턴 미완료이면 요약·새 결정 및 세션 성공 판정을 거부한다. 기존 주문/기존결정 재개는 변경하지 않는다.
- provider별 호출·오류·소요·토큰을 기록한다. Codex가 비용이나 구독률을 제공하지 않으면 미제공으로 표시하고 Claude 구독률과 합산하지 않는다. FAKE_CLAUDE 테스트에서는 실제 Codex도 절대로 실행하지 않는다.

## 화면과 검증

- 시스템 > 설정: Codex 토론 참여 ON/OFF, 역할, 준비 상태, 다음 새 분석부터 적용, 오류/사용량을 보여 준다. 데이터 > 시세·차트: 시장별 KIS 순위·거래대금·관측변화율·출처·시각·미수집/부분실패를 표시한다. 토론 상세에는 실제 provider를 표시한다.
- 합성 KIS 응답, 가짜 CLI, 임시 원장으로 TDD한다. 설치된 CLI/공식 문서의 플래그를 검증한다. 실주문·실거래 운영 시험·유료 API 신규 설정은 범위 밖이다.
- 일관된 동작을 전체 pytest(동시에1개), ruff, web typecheck/vitest/build, 독립 코드리뷰와 CI로 확인한다. 운영 main과 서비스의 업데이트/재시작은 변경사항과 검증 결과가 준비된 뒤 판단한다.

## 공식 근거

- https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/overseas_stock/trade_pbmn/trade_pbmn.py
- https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/overseas_stock/updown_rate/updown_rate.py
- https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/overseas_stock/price_detail/chk_price_detail.py
- https://learn.chatgpt.com/docs/non-interactive-mode
- https://learn.chatgpt.com/docs/config-file/config-reference
