# ingest

## 책임

- 외부 수집원(KIS 시세·뉴스·거시·DART·EDGAR·커뮤니티·채널)의 설정과 분석 유니버스·섹터 맵을 소유한다. `sources.yaml` 의 7키(`kis news macro dart edgar community channels`), `universe.yaml`, `sectors.yaml`, `schedule.yaml` 의 `news_collect` 를 섹션으로 등록한다.
- 원장 v3 `news_items`(시장 전체 뉴스 저장소)를 소유한다. 시장 전체 뉴스를 상시 쌓아 발굴·세션 입력이 읽는다(`news_store`: 멱등 적재 `insert_items`, 유니버스 종목명·코드 태깅, asof 창 읽기 `recent`, 유사 뉴스 판정 `novel_items`, 보존 정리 `prune`).
- 수집기: `news`(구글 RSS·네이버), `macro`(FRED·ECOS), `dart`·`edgar`, `community`·`youtube`·`telegram_channel`·`naver_blog`, `manual_inputs`, `disclosures`(공시 중요도), `indicators`(기술 지표 — 본문은 커널 `broker/indicators.py` 로 승격돼 있고 이 모듈은 재노출한다. ML·spot·fo 가 같은 계산을 공유한다). `inputs.build_inputs` 가 절(technical·fundamental·news_macro·sentiment)을 스레드풀로 모아 `00_inputs` 를 만들고 `collect_and_save` 가 저장한다. 세이브티커 jsonl 은 `saveticker_ingest` 가 뉴스 저장소로 옮긴다.
- 데몬 잡 `news_collect`(`job.py`, `schedule.yaml news_collect.interval_s` 기본 1800, heavy 밖): 시장별 질의 묶음(`news.market_queries`)을 돌려 적재 → 세이브티커 폴더 적재 → `store_retention_days`(14) 지난 행 prune → `news.post_ingest` 훅. 시장·세이브티커 실패는 그 항목만 `{"error": ...}` 로 남기고 잡은 계속하며, 훅 실패도 잡을 실패시키지 않는다(`out["hooks"]`).
- CLI: `trader ingest collect ...`(`cli.py`). `universe check|sectors` CLI 는 M7.
- 웹 자원(`routes.py`): `GET /news?market=&hours=48&limit=100[&ticker=]` → `{market, hours, total, asof, items}`(`news_store.recent` 행 그대로, `tickers[]` 포함, `url_hash` 없음 — 촉매·테마 라벨은 scan `GET /news/labels`), `GET /news/events?market=&hours=72` → `{market, hours, asof, disclosures}`(공시 행 + `severity`; 거시 지표는 원장에 없어 내지 않는다), `GET /news/manual?market=`·`POST /news/manual {market, text, url?}`(토큰 + 같은 출처, 첫 줄 제목·나머지 메모 → `manual_inputs.append_market`, 검증 오류 400). SSE 키 `news`(`news_items` 건수·최대 id). 메뉴 `data/news "뉴스·이벤트"`. 웹 알려진 차이: 옛 `/system/events` 는 공시와 거시를 함께 냈으나 `/news/events` 는 공시뿐이다(거시 관측은 원장에 저장되지 않음 — 저장소가 생기면 M12 후보, T10-R1). 옛 `/control/manual-inputs` → `/news/manual`(선택 `url`·`GET` 추가).

## 공개면 (`api.py`)

설정: `SourcesConfig`, `NewsCfg`, `MacroCfg`, `load_sources`. 유니버스: `Universe`, `UsSymbol`, `SectorMap`, `load_universe`, `load_sectors`. 수집 골격: `Section`, `run_section`, `skipped_section`, `owned_client`, `strip_tags`, `MAX_PAST`, `SKIP_REASONS`. 입력 조립: `InputDeps`, `build_inputs`, `collect_and_save`, `jsonable`, `SCHEMA_VERSION`, `build_input_deps`, `build_dart`, `build_edgar`. 클라이언트·수집기: `DartClient`, `DartError`, `ANNUAL`, `RATIO_KEYS`, `basic_ratios`, `EdgarClient`, `ratios_by_year_us`, `Fred`, `collect_macro`, `GoogleNewsRss`, `MACRO_QUERIES`, `NaverBlogSource`. 뉴스 저장소: `recent`, `count`, `novel_items`, `normalize_title`, `row_to_dict`, `insert_items`, `universe_names`. 서브모듈: `disclosures.critical_recent`, `manual_inputs.{read, read_market, append_market}`, `indicators.{COLUMNS, latest, indicator_frame, to_frame}`.

## 규약

- 모든 수집기는 `run_section` 으로 `Section{data, received_at, error}` 를 돌려주며 예외를 던지지 않는다(`skipped_section`, `SKIP_REASONS` 는 `errors` 집계에서 제외). `00_inputs` 키는 `technical·fundamental·news_macro·sentiment + meta + errors`; 구조를 바꾸면 `inputs.SCHEMA_VERSION` 을 올린다. 저장 전 Decimal→문자열, 읽는 쪽은 `Decimal(str)`. DART·EDGAR 비율은 같은 한글 키(`basic_ratios`)를 공유하고 EDGAR companyfacts 는 메모하지 않는다.
- 룩어헤드 방지: 모든 수집 항목에 `received_at`, `asof` 이후 발행분 제외(뉴스·커뮤니티·공시, EDGAR 는 `filed <= asof`). 뉴스 저장소 읽기는 `COALESCE(published_at, received_at) <= asof AND received_at <= asof`. 기술·수급은 `asof` 의 시장 현지 날짜 뒤 봉을 잘라 내고 `meta.last_candle_date` 를 남긴다. 재무비율은 사업연도 Y 값을 Y+1 년 4월 1일부터 적용. 과거 asof 재수집은 `MAX_PAST`(24시간) 넘으면 거부, 미래 asof 도 24시간 넘으면 거부.
- 뉴스 저장소: `url_hash` 는 시장 범위(`sha1("<market>|<url>")`)라 티커 없는 거시 뉴스는 KR·US 두 행이 된다. 태깅은 유니버스 종목명·코드만(ASCII 티커는 단어 경계, 2자 미만 무시) — 유니버스 밖은 scout 의 몫.
- 유사 뉴스 판정(`novel_items`): Jaccard ≥ 0.6 또는 SequenceMatcher ≥ 0.8, 숫자 토큰이 다르면 새 내용. 미판정이 `MAX_PENDING`(1000)을 넘으면 최신 1000건만 판정하고 더 오래된 밀린 항목은 `novel=0` 으로 닫는다. 전수 비교와 같은 결과여야 한다(테스트가 기준 구현과 대조).
- 공시 중요도(`disclosures`): KR 은 DART `list.json` 을 그날 total_page 까지(최대 15쪽), US 는 대상 종목 CIK 의 최근 3일 8-K. 보고서명 키워드(`DART_CRITICAL`·`DART_HIGH`, 해제·철회 등 `DART_NEGATE` 는 낮춤)·8-K item 으로 `severity`(critical·high·info)를 매겨 뉴스 저장소(source `dart`|`edgar`)에 멱등 적재.
- 소스 정책은 `sources.yaml` 기본값이 출처다(Reddit·클리앙 on, DC·에펨코리아는 구현하되 off). 네이버 뉴스는 NAVER API HUB 키(`NAVER_CLIENT_ID/SECRET`). `EdgarCfg.user_agent` 는 비워 두고 `secrets/.env` 의 `EDGAR_USER_AGENT` 를 쓴다(공개 저장소에 연락처를 두지 않는다). 키워드 매칭은 ASCII 티커만 단어 경계. 세이브티커 수집기는 saveticker 기능이 소유하고, ingest 는 `saveticker.api` 로 폴더를 읽어 적재만 한다.
- 유니버스·섹터의 교차 필드 검증은 `load_universe`·`load_sectors` 가 전체 모델로 한다. 커널 섹션 등록은 템플릿 로드·키 검증용이다.
- 코드는 `kernel/`, 자기 자신, `saveticker.api` 만 import 한다(3층 규칙). 외부 사이트 원문은 fixture 로 커밋하지 않는다(합성 데이터).

## 테스트

`tests/features/ingest/` — 수집기 모듈별 `test_*`(`test_news_store`, `test_dart`, `test_edgar`, `test_macro`, `test_inputs` 등), 설정·유니버스(`test_settings`, `test_universe`), 마이그레이션(`test_migrations`), CLI(`test_cli`), 데몬 잡(`test_job`: 등록·시장 격리·prune·훅 실패 격리·세이브티커 적재), feature·공개면(`test_feature`, `test_public_api`). HTTP 는 respx 로 막고 fixture 는 합성 데이터(`fixtures/`).

## 알려진 차이 (옛 저장소 대비)

1. 설정 `news.saveticker_*` → `saveticker` 섹션(saveticker 기능 소유).
2. `disabled_sites` 를 삭제했다.
3. `edgar.user_agent` 는 선택(기본 `""`, 시크릿 `EDGAR_USER_AGENT` 우선).
4. `channels.naver_blog` 기본 `[]`(옛 `["ranto28"]`). 사용자 확인 TODO.
5. CLI `trader collect` → `trader ingest collect`. 시세 클라이언트 없이 돈다(계좌 조립은 accounts, M4 레인이 주입).
6. `fundamental.peer` 는 `InputDeps.peer_fn` 이 없으면 `no sector peers` 로 건너뛴다(M7).
7. `news_collect` 는 수집·적재·prune·`news.post_ingest` 훅만 한다. `_news_trigger`·`_disclosure_trigger`·`_events_check`·`discover` 큐·`ticker_names` 캐시는 M7, `_vix` 는 M6, 웹 `/api/news`·수동 입력 라우트는 M11(`routes.py`), health `disclosure` 는 M12.
8. `universe.yaml`·`sectors.yaml` 모델은 ingest 소유(PIT 기록·`universe check|sectors` CLI 는 M7).
9. `_row/_jsonable/_matches/_iso_to_utc/_kst_to_iso` 는 공개 이름으로 바꾸고 옛 이름 별칭을 둔다.
10. `USER_AGENT` 브랜딩을 바꿨다.
