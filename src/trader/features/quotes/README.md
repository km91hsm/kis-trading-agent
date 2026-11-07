# quotes

## 책임

- 화면 시세(tier=service, 조회 전용 — 웹 프로세스는 주문을 내지 않는다): 국내 실시간 허브(`hub.QuoteHub`, KIS 웹소켓 하나), `/quotes*` 웹 자원(`routes.py`), 종목명(`names.py`)·차트(`charts.py`) 웹 자원, 환율 원천·하루 캐시(`fx.py`).
- 데몬의 `market_activity` 잡은 KIS 거래대금·등락 순위를 기본30초마다 수집한다. migration16의 배치·분할 메타데이터를 원자적으로 append-only 저장하며 성공/실패 조회 인덱스로 과거 JSON 전체 스캔을 피한다. 화면과 다른 기능은 `api.activity_snapshot` / `market_activity` 포트 또는 `GET /quotes/activity?market=KR|US`로 원장만 읽는다.
- `change_pct`와 `growth.value`는 소수 비율이다(0.025=2.5%). 금액은 Decimal 문자열, 결측은 null. 누적 거래대금 3경계로 최근5분/직전5분을 비교하며 경계오차60초·동일거래소/통화/운영일/관측창을 강제한다. 상세 계산·자료제약은 [운영 안내](../../../../docs/market-data-codex.md)를 참고한다.
- 시세 클라이언트는 시크릿의 공용 실전 키(`load_secrets().kis_creds(env="real")` → `kernel.broker.factory.build_market`)로 직접 만든다. 공용 실전 키가 없으면 시세 클라이언트·허브 없이 빈 값·스냅샷만 돌려준다. 런타임(시세 클라이언트 + 허브)은 원장 위치(`WebDeps.state`)마다 프로세스에 한 번(`routes.runtime_for`, 테스트는 `routes.install/reset`).
- quotes 는 accounts 를 import 하지 않는다(accounts 통합 현황이 `quotes.api.fx_rate` 를 쓰므로 순환 방지). 다른 기능은 `ingest.api`(FRED·유니버스)·`scan.api`(관심종목, 검색 순위)만 쓴다. 레인은 import 하지 않고 원장 표(`ticker_names`·`ticker_exchanges`·`decisions`·`orders`·`fills`·`positions_snapshot`)만 읽는다 — 쓰기는 데몬 잡 `names_fill` 의 `ticker_names` 하나(이름 없는 행에만 INSERT).

## 공개면 (`api.py`)

- `prepare_fx(deps) -> None` — quotes 런타임(과 KIS 환율 원천)을 원장 위치마다 한 번 조립. `fx_rate` 를 quotes 경로보다 먼저 부르는 화면이 쓴다.
- `fx_rate(settings, conn, pair="USDKRW") -> dict | None` — `{pair, rate: str(Decimal), as_of, source: "kis"|"macro"}`, 원천이 없거나 모르는 쌍이면 None. 원천이 모두 실패하면 `fail_at` 을 남기고 `fx.FAIL_TTL_S`(300초) 동안 다시 묻지 않으며, 그동안·실패 때 이전 날짜 값이 있으면 `stale: true` 를 붙여 준다.

## 데몬 잡 (`jobs.py`)

- `names_fill`(interval, schedule.yaml `names_fill.interval_s` 기본 3600초, 기동 120초 뒤 첫 실행): ① 설정 `quotes.instrument_names`(`"<KR|US>:<종목>": 이름`, 기본 `{}` — DART·EDGAR 캐시에 없는 헤지·현금·지수 대용 ETF 등)를 원장 `ticker_names` 에 `source='config'` 로 적재(**설정이 관측보다 우선** — 잔고·브로커 이름이 있어도 덮고, 설정 이름을 바꾸면 다음 실행에 반영; 같은 값이면 쓰기 없음, Ruling T18-R3) ② 원장 `positions_snapshot`(현물, `_CASH*` 제외)·`orders`(asset `stock`)에 나왔지만 이름이 없는 종목을 `ports["accounts"].market_client()` 의 `get_stock_name`(US 는 `ticker_exchanges` 거래소)으로 조회해 `source='broker'` 로 남긴다. ②는 이미 이름이 있는 종목(잔고 관측·설정)을 덮지 않는다. 못 찾은 종목은 control `names_miss`(`{"<시장>:<종목>": KST 날짜}`, `control_namespaces`)로 그날 다시 묻지 않고, 한 번에 20건(`LIMIT`)까지. 시세 클라이언트가 없으면 ①만 하고 `{"skipped": "시세 클라이언트 없음", "config": n}`. 결과 `{config, candidates, asked, filled}`.
- 옛 대비: 옛 `names.fill_missing` 은 캐시 JSON(DART·EDGAR)으로 먼저 채우고 결정(`decisions`) 종목도 봤으나, 새 잡은 보유·주문 종목만 보고 캐시 적재는 하지 않는다(커널 `names.resolve` 는 읽기 전용 그대로). source `kis` → `broker`, 설정 `instrument_names` 는 `{시장: {코드: 이름}}` → `quotes.instrument_names` 평면 `"<시장>:<코드>"` 키. `ticker_names` 쓰기 helper 는 spot `ledger.upsert_ticker_name` 이 있으나 레인이라 import 할 수 없어 `jobs._insert_name`(이름 없는 행에만, broker 용)·`jobs._fill_config`(설정 우선 upsert)를 둔다.

## 웹 자원 (`routes.py`, 토큰 없음)

- `GET /quotes?market=KR|US&tickers=a,b[:NAS|NYS|AMS]`(≤20) → `{session, at, quotes: {ticker: {price, change_pct, at, stale, source: "ws"|"rest"|"snapshot"}}, source: "ws"|"rest", stream, hub_yield, next_poll_ms}`. 국내 정규장은 허브 틱 먼저, 틱 없는 종목만 REST(5초 TTL 캐시, 종목별 락으로 동시 미스도 KIS 1회), 장 밖·조회 실패는 마지막 캐시(stale) 또는 최신 현물 보유 스냅샷 현재가(`source: "snapshot"`).
- `GET /quotes/indices` → `{session, at, indices: {KOSPI|KOSDAQ: …}, next_poll_ms}`(허브 먼저, 없는 지수는 REST `get_index_kr`).
- `GET /quotes/book?ticker=` → `{session, ticker, book, next_poll_ms}`(허브 호가 10단 임차 10초, 없으면 REST `get_orderbook_kr`, 장 밖 null).
- `GET /quotes/fo?codes=` → `{session, at, quotes: {code: {kind, price, change_pct, bid, ask, at, stale, source}}, kospi200, next_poll_ms}` — REST 만, 종류는 `settings.fo.product_cfg()` 접두(`kernel.broker.fo_codes.kind_of`), 모르는 코드 400.
- `GET /quotes/fx?pair=USDKRW` → `fx_rate` 결과, 원천이 없으면 404, 모르는 쌍 400.
- `next_poll_ms`: 국내 정규장 1000 · 미국 정규장 `settings.quotes.us_poll_ms`(5000) · 파생 정규장 `settings.quotes.fo_poll_ms`(2000) · 장외 60000. SSE 키 없음. 메뉴 `data/quotes "시세·차트"`.

## 종목명·차트 (`names.py`·`charts.py`, 토큰 없음)

- `GET /names?market=&tickers=a,b` → `{"<시장>:<종목>": 이름}`(모르는 종목은 키 없음). tickers 없으면 원장 `ticker_names` 전체(market 필터), 있으면 market 필수·티커 형식 검사 후 원장 → 캐시 JSON(`state/cache` DART·EDGAR 맵). 네트워크 없음.
- `GET /names/search?q=&market=&limit=20` → `[{market, ticker, name, exchange, source}]`. 코드 접두 > 이름 접두 > 이름 부분 > 코드 부분, 같으면 `held` > `watch` > `universe` > `ledger` > `listed`.
- `GET /charts?market=&ticker=&exchange=&days=120`(1~1000) → `{ticker, market, candles: [{time: 날짜, open, high, low, close, volume}], markers: [{time, kind: "fill", side, text}], lines: {stop_loss, take_profit, date}}`. 봉은 quotes 런타임의 시세 클라이언트(없으면 503, KIS 오류 502)로, (시장·종목·거래소·일수·KST 날짜) 300초 캐시. 마커는 `fills` ⨝ `orders`(현물), 선 값은 그 종목 최신 결정 `decisions.data_json` 의 `stop_loss/take_profit`.
- `GET /charts/minute?market=&ticker=&exchange=&count=120`(1~390, US 는 최대 120) → `{ticker, market, interval: "1m", tz, session_date, candles: [{time: UTC epoch…}], markers}`. 60초 캐시, 캐시 미스는 키별 락 안에서 다시 확인해 동시 요청도 KIS 1회. 마커는 session_date(시장 현지) 하루의 체결(UTC 경계를 SQL 로), 분 단위로 내린 epoch + 현지 `HH:MM` 텍스트.
- US 거래소는 `NAS|NYS|AMS`, 안 주면 유니버스 → 없으면 NAS. 테스트용 캐시 비우기 `charts.reset()`.

## 운영 점검·상태 (M12)

- health 영역 `quotes`: `quotes.session_busy.<env>`(watch) — 체결통보 리스너가 KIS 웹소켓 세션을 쥔 동안(control `ws_session_busy:<env>` until 이 미래) 시세 허브가 양보 중. 옛 웹 임시 판정의 busy 사유를 옮긴 것(PF-1).
- status 절은 없다(`/status`·STATUS.md 에 quotes 절 없음).

## 규약

- **허브**: 41칸 중 `max_tickers`(40) = 지수 2 + 호가 1 + 체결가 37(최근 요청 순), 임차 `lease_s` 30초(호가 `book_lease_s` 10초), `idle_s` 60초 무요청·장 밖이면 스레드 종료(스레드 하나 `kis-ws-quotes`, 연결은 그 스레드만). 접속 실패·끊김·접속키 거절은 지수 백오프(최대 30초), 거절 종목은 10분 제외, 10초 넘은 틱은 REST 로 보완. 표시 전용 — 신호·주문·게이트는 읽지 않는다.
- **세션 양보**: KIS 는 앱키당 웹소켓 세션 하나. 데몬의 체결통보 리스너(`spot.notice`)가 시세와 같은 실전 앱키 세션을 쥐면 control `ws_session_busy:real` = `{"until": UTC ISO}`(지금+90초, 60초마다 갱신, 놓을 때 삭제)를 쓰고, 허브 `busy()`(`hub.session_busy`, 원장 2초 캐시)는 until 이 미래인 동안 붙지 않는다(`status()["yield_reason"] = "ws_session_busy"`, 다른 연결이 쥐면 `appkey_in_use`).
- **환율**(`fx.py`): KIS `FHKST03030100`(종목_지수_환율기간별시세, 시장 `X`, 종목 `settings.quotes.fx_code` 기본 `FX@KRW`, 계좌 불필요, 최근 7일 KST 범위 `output1.ovrs_nmix_prpr` → 0 이면 `output2[0]`) → KIS `HHDFS76200200`(현재가상세 `NAS`/`AAPL` 1회) `output.t_rate` → 거시(FRED `DEXKOUS`, `FRED_API_KEY` 없으면 원천 없음) → None(404). KIS 두 단계는 주입점 `fx.kis_fx` 에 런타임 조립 때(`routes._build_runtime`, 시세 클라이언트가 있을 때만) 붙고 응답은 `source: "kis"`. 계좌 개요(`/accounts/overview`)는 `api.prepare_fx(deps)` 로 런타임을 먼저 조립해 FRED 값이 하루 캐시를 먼저 차지하지 않게 한다. 실기 확인 항목 3개(`FX@KRW` vs `FX@KRWKFTC` 값 차이, `ovrs_nmix_prpr` 자릿수·갱신 주기, 하루 범위 빈 응답)는 `docs/research/2026-11-05-kis-fx-rate-api.md` 참조. 결과는 control `fx_cache:<pair>`(`control_namespaces=("fx_cache",)`)에 KST 날짜와 함께 저장해 하루 1회만 원천을 부른다(실패는 `fail_at` 로 `FAIL_TTL_S`=300초 음성 캐시, 이전 레코드가 있으면 `stale: true`).
- 설정: `settings.yaml` `quotes: {realtime_ws: true, us_poll_ms: 5000, fo_poll_ms: 2000, fx_code: "FX@KRW"}`(`settings.QuotesSettings`). `realtime_ws: false` 면 국내도 REST 폴링만.
- 허브는 데몬 스레드라 웹 앱 종료 때 따로 내리지 않는다(유휴 60초면 스스로 끝난다).

## 테스트

`tests/features/quotes/`: `test_hub.py`(옛 허브 18건 + 세션 양보 2건, 가짜 연결·시계), `test_routes.py`(옛 자원 13건 + poll·파생·키 없음), `test_fx.py`(kis→macro→404·하루 캐시·FRED), `test_fx_kis.py`(KIS 환율 연결·`fx_code`·개요 선조립), `test_names_charts.py`(종목명·검색·일봉 마커·선·분봉 락·캐시). 네트워크 없음.

웹 알려진 차이: 환율 KIS TR 은 공식 샘플 기준으로 붙였고 실기 값은 미확인이다(T9-R2/R5). 미국 시세 전용 경로(`/quotes/us`)는 없다 — `GET /quotes?market=US`(R4). 웹 프로세스에서 KIS 클라이언트를 쥐는 유일한 예외(표시 전용, 앱키·토큰 캐시 공유 `TRADER_KIS_SHARED_DIR`).

옛 대비: `/names` 는 캐시 적중을 원장 `ticker_names` 에 쓰지 않는다(웹은 읽기 전용). 차트는 계좌 필터(`account_id`)·결정·트리거 마커를 빼고 체결 마커 + 손절·목표 선으로 바꿨다.
