# scan

## 책임

- 오늘 '불이 붙은' 테마를 점수로 고르는 서비스 기능(tier=service). 감지·기록·알림만 하고 매매는 하지 않는다(분석 세션 → 게이트 경로).
- 설정: `settings.yaml` 의 `theme`(`ThemeCfg`: `enabled`·`top_n`·`min_cluster_titles`·`weights`=`ThemeWeights`), `schedule.yaml` 의 `theme.interval_s`(`ThemeSchedule`, 300~86400, 기본 1800).
- 원장 v11 `theme_heat`(`migrations.py`): (시장·날짜·테마)당 1행, 그날 최고 점수 관측 한 벌과 5거래일 뒤 채점 `fwd_ret_5d`.
- 테마 열기(`theme.py`): 신호 5개(새 뉴스 증가·거래량/등락 순위·외인기관 순매수·세이브티커 순위 상승·`vol_z`)를 테마(섹터 대분류 + 뉴스 제목 키워드 군집)별로 0~1 정규화해 가중합. `compute` → `record`(control `theme_heat:<m>` + 원장, 상위 집합이 바뀔 때만 알림 문구) → `mature`(구성 종목 평균 수익 − 1/N 벤치마크).
- 조회: `theme_of_map`·`members_of`(종목 → 상위 테마들, 오늘 기록만).
- 사건 감지(`events.py`): 뉴스 키워드 건수 ≥ `min_news` 인 사건을 `control.events:<m>` 에 기록(shock 만 테마 안전장치). `events_check`(`jobs.py`)가 감지→기록→처음 본 사건만 알림.
- 소속 조회(`membership.py`): `of`·`for_ticker` — 종목의 오늘 상위 테마·shock 사건, `asof` 뒤 관측 기록은 제외(룩어헤드 방지).
- 큐 kind `discover`(장중 발굴)·텔레그램 `/watch`(Task 11). 훅: `news.post_ingest` → `triggers.post_ingest`(시장별 `events_check` 뒤 `deps` 가 있으면 공시·뉴스 트리거).
- 설정 파일 `events`(`templates/config/events.yaml`, 사건 31개): `enabled`·`lookback_hours`·`max_picks_per_event`·`max_event_picks`·`events`(`EventDef`, id 중복 거부). `load_events()`.
- 잡(`jobs.py`): `{market}_theme`(`THEME`, interval=`schedule.theme.interval_s`, 게이트 `monitor`, `theme.enabled` 일 때만) — `theme_body` 가 `compute`→`record`→상위가 바뀔 때만 알림.
- 포트(`ports.py`): `ports["scan"]` = `ScanPortImpl.theme_of(conn, market, today)` → 종목 → 오늘 상위 테마·shock 사건 라벨(없으면 None). spot 게이트가 쓴다.
- CLI(`cli.py`): `trader scan theme show [--market KR] [--live]`, `trader scan events show [--market KR]`(읽기 전용 JSON).
- 문구(`messages.py`): `theme_heat`·`theme_name`·`event_detected`.
- 다른 기능의 표를 읽는 데이터 접근: `ingest` 의 `news_items`, `spot` 의 `ticker_names`(`SELECT ticker, name FROM ticker_names WHERE market=?`).

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
8. (M7) `scan backtest` 의 왕복비용은 `backtest.ROUND_TRIP_BP = {"KR": 30, "US": 50}` — spot 의 `costs` 는 레인 모듈이라 scan 이 못 쓰고 값만 복제했다(변경 시 두 곳을 같이). ML 랭커(`ranker.py`)는 import 순환을 피해 ml 이 아니라 scan 에 둔다. 옛 `screen backtest` 는 `scan backtest`.
