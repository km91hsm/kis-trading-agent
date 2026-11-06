# scan

## 책임

- 오늘 '불이 붙은' 테마를 점수로 고르는 서비스 기능(tier=service). 감지·기록·알림만 하고 매매는 하지 않는다(분석 세션 → 게이트 경로).
- 설정: `settings.yaml` 의 `theme`(`ThemeCfg`: `enabled`·`top_n`·`min_cluster_titles`·`weights`=`ThemeWeights`), `schedule.yaml` 의 `theme.interval_s`(`ThemeSchedule`, 300~86400, 기본 1800).
- 원장 v11 `theme_heat`(`migrations.py`): (시장·날짜·테마)당 1행, 그날 최고 점수 관측 한 벌과 5거래일 뒤 채점 `fwd_ret_5d`.
- 테마 열기(`theme.py`): 신호 5개(새 뉴스 증가·거래량/등락 순위·외인기관 순매수·세이브티커 순위 상승·`vol_z`)를 테마(섹터 대분류 + 뉴스 제목 키워드 군집)별로 0~1 정규화해 가중합. `compute` → `record`(control `theme_heat:<m>` + 원장, 상위 집합이 바뀔 때만 알림 문구) → `mature`(구성 종목 평균 수익 − 1/N 벤치마크).
- 조회: `theme_of_map`·`members_of`(종목 → 상위 테마들, 오늘 기록만).
- 사건 감지(`events.py`): 뉴스 키워드 건수 ≥ `min_news` 인 사건을 `control.events:<m>` 에 기록(shock 만 테마 안전장치). `events_check`(`jobs.py`)가 감지→기록→처음 본 사건만 알림.
- 소속 조회(`membership.py`): `of`·`for_ticker` — 종목의 오늘 상위 테마·shock 사건, `asof` 뒤 관측 기록은 제외(룩어헤드 방지).
- 큐 kind `discover`(장중 발굴, `jobs.discover_request`)·텔레그램 `/watch`(관심종목). 훅: `news.post_ingest` → `triggers.post_ingest`(시장별 `events_check` 뒤 `deps` 가 있으면 공시·뉴스 트리거).
- 설정 파일 `events`(`templates/config/events.yaml`, 사건 31개): `enabled`·`lookback_hours`·`max_picks_per_event`·`max_event_picks`·`events`(`EventDef`, id 중복 거부). `load_events()`.
- 잡(`jobs.py`): `{market}_theme`(`THEME`, interval=`schedule.theme.interval_s`, 게이트 `monitor`, `theme.enabled` 일 때만) — `theme_body` 가 `compute`→`record`→상위가 바뀔 때만 알림.
- 포트(`ports.py`): `ports["scan"]` = `ScanPortImpl.theme_of(conn, market, today)` → 종목 → 오늘 상위 테마·shock 사건 라벨(없으면 None). spot 게이트가 쓴다. `watchlist(conn, market)` → 런타임 관심종목(`/watch`·웹, market 을 주면 그 시장 종목만 접두사 없이) — scalp 후보 소스(M9).
- CLI(`cli.py`, `uv run trader scan --help`): `trader scan theme show [--market KR] [--live]`, `trader scan events show [--market KR]`(읽기 전용 JSON), `trader scan run [--market --asof --dry-run]`(`--dry-run` 은 screener 세션 없이 정량 상위만)·`backtest`·`universe check|sectors`(아래 M7 절).
- 문구(`messages.py`): `theme_heat`·`theme_name`·`event_detected`.
- 다른 기능의 표를 읽는 데이터 접근: `ingest` 의 `news_items`(라벨용 id 조회 포함), `spot` 의 `ticker_names`(`SELECT ticker, name FROM ticker_names WHERE market=?`).
- 웹 자원(`routes.py`): `GET /news/labels?ids=1,2`(≤500) → `{id: {catalyst: is_catalyst(source), themes: [그 시장 최신 테마 열기 상위 top_n 중 기사 종목이 속한 테마]}}`(없는 id 는 빠진다), `GET /news/themes?market=&n=10` → `{market, date, observed_at, top_n, themes}`(control `theme_heat:<m>` 의 `top(heat, n)`), `GET /settings/watchlist` → `{tickers}`, `PATCH /settings/watchlist {tickers}`(토큰 + 같은 출처, `set_watchlist(channel="web", actor=토큰 지문)`, 형식 오류 400). SSE 키 `theme`(`theme_heat` 건수·최대 id·최신 observed_at)·`watchlist`(저장된 목록). 메뉴 `data/themes "테마"`·`data/watchlist "감시목록"`(데이터 「테마·감시목록」 페이지). 웹 알려진 차이: 옛 `/system/theme` → `/news/themes`, 옛 `/control/watchlist` → `PATCH /settings/watchlist`. 기사 라벨(`/news/labels`)은 ingest 행과 갈라 scan 이 붙인다.

## 규칙

- 코드는 `trader.kernel.*` 와 다른 기능의 `api.py` 만 import 한다(3층 규칙). 로거 `trader.scan`.

## 알려진 차이 (옛 저장소 대비)

1. `ThemeCfg.interval_s` 는 없다. 주기는 `schedule.yaml theme.interval_s`.
2. `mature` 는 `state` 대신 `close_table(market, since, until)` 콜러블을 주입받는다(피처 스토어 종가는 M8). None 이면 경고하고 0 을 돌려준다. 수익 창 계산은 `memory.outcomes.window_return` 대신 이 모듈의 private 구현(pandas import 없음).
3. `compute` 의 `vol_z` 는 피처 스토어 `vol_z_20`(M7, `ml.api`)에서 채운다. `state` 인자는 없고 기본 상태 폴더를 쓴다. 섹터 맵은 `sector_of` 로 주입하고, 없으면 `ingest.api.load_sectors()` 를 시도하다 실패하면 키워드 테마만 쓴다.
4. `events check`(수혜 표 시세 확인 CLI)는 이식하지 않았다. 세이브티커 폴더는 `saveticker.api.load_cfg().dir`(옛 `sources.news.saveticker_dir`)이다.
5. discovery(scout 후보 검증·병합·`event_watch` 입력)는 M7 로 이연 — 옛 test_events 의 discovery 관련 단언은 이식하지 않았다. 옛 게이트 테스트(shock 종목 테마 안전장치)는 `tests/features/spot/test_executor_theme.py` 로 이식했다(`ports["scan"]`). 사건 감지는 데몬 직접 호출 대신 `news.post_ingest` 훅으로 돈다.
6. 알림 순서는 옛 코드 그대로 "알림 완료 표시(`new_for_alert`) 후 전송"이다. 전송이 실패해도(로그 debug) 그 사건은 그날 다시 알리지 않는다. `notifier=None` 이면 전송만 건너뛰고 `alerted` 는 채워진다.
7. `compute` 의 섹터 맵 폴백 `_default_sector_of` 의 예외는 로그 없이 키워드 테마로 넘어간다. `mature(close_table=None)` 경로는 테스트가 없다(M8 에서 종가표 주입과 함께 정리).
8. (M7) `scan backtest` 의 왕복비용은 `backtest.ROUND_TRIP_BP` — 커널 `execution/costs.ROUND_TRIP_BP`(KR 30bp·US 50bp)를 다시 내보낸다(M8 A5, 정본은 커널 한 곳). ML 랭커(`ranker.py`)는 import 순환을 피해 ml 이 아니라 scan 에 둔다. 옛 `screen backtest` 는 `scan backtest`.

## M7 확장 (정량 필터·랭커·발굴·스크리너·트리거)

- 정량 필터(`quant.py`): 피처 스토어 최신 행(`latest_features`: asof 이하, 종목별 최신)에서 거래대금 하위 30%·변동성 상위 10% 를 거르고 `roc_10`·`high_52w_pos`·`turnover` 신호 z 합으로 `score`(상위 `TOP_N`=30). `drop_reasons` 는 탈락 사유 한 문장. `candidates` 는 유니버스(+`watch`+런타임 관심종목)를 점수순으로, 피처 없는 종목은 점수 None 으로 끝에.
- ML 랭커(`ranker.py`): `ml.api.latest_model` 의 승격 모델이 있고 `ml.yaml models.ranker` 가 켜져 있을 때만 `(rank_fn, "quant@버전")` 을 돌려주고 정량 점수와 `ML_WEIGHT`=0.6 으로 섞는다. 승격 모델이 없으면 정량 점수만(None).
- 발굴(`discovery.py`): `build_inputs`(뉴스·세이브티커·공시·KR 네이버 블로그·사건 수혜 `event_watch`·테마 열기를 `_discovery_inputs` 로 저장) → scout 에이전트 → `resolve`(파이썬이 코드·거래소를 확정, 유니버스 밖 발굴은 하루 `MAX_LISTED`=2) → `merge`(screener 입력 순서). `summary`·`line` 은 알림 한 줄.
- 스크리너(`screener.py`·`run.py`): `run_screen` = 정량 후보 + 발굴 병합 → screener 세션(kind `screen`) → `analyze_targets`(picks + 보유 + watch). `run_scan(ctx, market=…)` 가 `_scan.json` 을 쓰고 `trader scan run` 과 spot `{market}_scan_analyze` 가 같은 함수를 쓴다. 세션 종류 `screen`·`discover`, 에이전트 scout·screener·scan-orchestrator(md 는 `agents/`), 도구 `trader tool scan discovery-inputs`·`submit-discovery`·`submit-screen` 3종.
- 유니버스 점검(`universe_check.py`): `trader scan universe check [--market]` — 종목이 KIS 시세를 실제로 주는지 확인(US 는 NAS→NYS→AMS 거래소 제안), 문제면 exit 1. 일시 오류(`TRANSIENT_CODES`: 초당 한도·네트워크·인증·HTTP 5xx)는 종목 문제와 구분해 note 에 적는다.
- 섹터 제안(`sector_suggest.py`): `trader scan universe sectors` — KIS 업종으로 `sectors.yaml` 에 빠진 종목을 제안(`apply` 는 멱등 덧붙임, 표에 없는 업종이 있으면 exit 1). `resolver`/`auto_classify` 는 yaml 우선·자동 캐시(`state/cache/sector_auto.json`) 보조로 `ports["scan"].sector_of` 를 채운다. `auto_classify` 는 `ports["scan"].classify` 가 감싸고, spot 잔고 갱신(데몬 `balance_refresh`·CLI `spot balance refresh`)이 보유 종목으로 부른다(M12 T17, 종목명은 `ticker_names` 로 ETF 상표 판정, US 는 잔고 `Position.exchange` 로 조회). 조회 실패·거래소 모르는 US 빈 결과는 같은 파일의 음성 캐시 `_failed:<시장>` 에 시각을 남겨 같은 날(KST) 다시 조회하지 않는다.
- 관심종목(`watchlist.py`, 텔레그램 `/watch add|remove|list`): 형식 검증 후 control 플래그 `watchlist` 에 저장, 후보·분석 대상에 합류. `KR:`/`US:` 접두사 선택.
- 백테스트(`backtest.py`): `trader scan backtest` — `roc_10` 상위 N·실제 scan 점수 상위 N·필터 통과 1/N 을 다음 거래일 진입·horizon 보유·같은 왕복비용으로 비교(겹치지 않는 창). 읽기 전용.
- 트리거(`triggers.py`): `news.post_ingest` 훅에서 새 뉴스가 붙은 종목의 재분석(`news_reanalyze`: 오늘 `30_decision` 이 있으면 트리거 kind `news` + 큐 `reevaluate`), 공시 중요도(`disclosure_trigger`: Dart/Edgar 공시를 적재, 처음 보는 중요 공시만), 장중 발굴(`news_trigger` → 큐 `discover`, 설정 `discovery.intraday`).
- 설정: `settings.yaml` 의 `discovery`(`DiscoveryCfg`: `intraday`=`IntradayDiscoveryCfg`·`similarity`=`SimilarityCfg`), 그 밖에 `theme`·`events` 는 위 그대로.

## 알려진 차이 (M7, 옛 저장소 대비)

- ML 랭커는 ml 이 아니라 scan 이 소유한다(Ruling A1 — ml → scan import 순환 방지, scan 이 `ml.api` 를 소비).
- `backtest.ROUND_TRIP_BP` 는 커널 `execution/costs.ROUND_TRIP_BP` 의 재노출이다(M8 A5 로 커널 승격 — M7 Ruling P1 의 복제 해소). 값은 커널에서만 고친다.
- 옛 결함 보존(Ruling P2): `discovery.merge` 와 `resolve` 의 `max_event` 기본값이 서로 다르다(8 / 0); `_discovery_inputs` 의 `schema_version` 은 1 로 고정; `news_trigger_at` 갱신·novel 집계가 enabled/일시정지/창 밖 건너뛰기보다 먼저 일어난다(감속 시 `analyze` 는 None = 큐 미등록); 공시 seen 목록은 300건 상한.
- 옛 결함 수정(Ruling T10-1): 옛 `scan_line` 이 요약된 discovery(미해결 = 개수 int)를 다시 요약하다 TypeError 로 알림이 조용히 사라졌다 — `discovery.summary` 가 int 를 받도록 고쳤다.
- 트리거 본문은 데몬 안이 아니라 `news.post_ingest` 훅(`triggers.post_ingest`)에서 돈다. 훅은 시장별로 오류를 격리한다. 훅마다 Dart/Edgar 클라이언트를 만들고 닫지 않는다(브리프 지정).
- FAKE 러너(`fake.py`)는 KR 시세 점검을 하지 않는다(`market_client=None`).
- `run_scan` 의 http 는 `ingest_api.owned_client(ctx.ext.get("http"), timeout=15)` — 주입분은 그대로 쓰고 직접 만든 것만 닫는다(Ruling T10-2; CLI 경로도 블로그 입력을 포함한다 — 옛 CLI 보다 개선). `scan run` 의 잘못된 `--asof` 는 `오류: …` exit 1.
- 발굴의 세이브티커 폴더는 `saveticker.api.load_cfg().dir`(Ruling T9-1; 옛 `sources.news.saveticker_dir`). `scan-orchestrator.md` 머리·금지 절은 scan 용으로 조정했고 discover/screen 절·공통 규칙은 옛 그대로다.
- 옛 `screen backtest` 는 `trader scan backtest`; 스캔 실행은 `trader scan run`, 유니버스 점검·섹터 제안은 `trader scan universe check|sectors`.
- 섀도 포트폴리오 기록은 없다(M8, spot `scan_analyze.post` 훅 자리). `events check` CLI 는 이식하지 않았다(`news.post_ingest` 훅이 사건 감지를 대신한다).
- `scan.api` 가 `triggers` 를 import 해 api import 때 accounts/regime/ingest api 가 로드된다. `jobs.discover_request` 는 순환을 피해 지연 import 로 `events_check` 를 부른다.

- M8 이후: `theme.mature`/`mature_views` 를 evaluation reflect 가 호출한다(종가표 `ml.api.close_table` 주입). 섀도 포트폴리오 기록은 evaluation 이 `scan_analyze.post` 훅으로 소비한다.
