# scan

## 책임

- 오늘 '불이 붙은' 테마를 점수로 고르는 서비스 기능(tier=service). 감지·기록·알림만 하고 매매는 하지 않는다(분석 세션 → 게이트 경로).
- 설정: `settings.yaml` 의 `theme`(`ThemeCfg`: `enabled`·`top_n`·`min_cluster_titles`·`weights`=`ThemeWeights`), `schedule.yaml` 의 `theme.interval_s`(`ThemeSchedule`, 300~86400, 기본 1800).
- 원장 v11 `theme_heat`(`migrations.py`): (시장·날짜·테마)당 1행, 그날 최고 점수 관측 한 벌과 5거래일 뒤 채점 `fwd_ret_5d`.
- 테마 열기(`theme.py`): 신호 5개(새 뉴스 증가·거래량/등락 순위·외인기관 순매수·세이브티커 순위 상승·`vol_z`)를 테마(섹터 대분류 + 뉴스 제목 키워드 군집)별로 0~1 정규화해 가중합. `compute` → `record`(control `theme_heat:<m>` + 원장, 상위 집합이 바뀔 때만 알림 문구) → `mature`(구성 종목 평균 수익 − 1/N 벤치마크).
- 조회: `theme_of_map`·`members_of`(종목 → 상위 테마들, 오늘 기록만).
- 사건 감지(`events.py`): 뉴스 키워드 건수 ≥ `min_news` 인 사건을 `control.events:<m>` 에 기록(shock 만 테마 안전장치). `events_check`(`jobs.py`)가 감지→기록→처음 본 사건만 알림.
- 소속 조회(`membership.py`): `of`·`for_ticker` — 종목의 오늘 상위 테마·shock 사건, `asof` 뒤 관측 기록은 제외(룩어헤드 방지).
- 훅: `news.post_ingest` → `events_post_ingest`(시장별 `events_check`, `notifier` kwarg 사용, 나머지 kwargs 무시).
- 설정 파일 `events`(`templates/config/events.yaml`, 사건 31개): `enabled`·`lookback_hours`·`max_picks_per_event`·`max_event_picks`·`events`(`EventDef`, id 중복 거부). `load_events()`.
- 문구(`messages.py`): `theme_heat`·`theme_name`·`event_detected`.
- 다른 기능의 표를 읽는 데이터 접근: `ingest` 의 `news_items`, `spot` 의 `ticker_names`(`SELECT ticker, name FROM ticker_names WHERE market=?`).

## 규칙

- 코드는 `trader.kernel.*` 와 다른 기능의 `api.py` 만 import 한다(3층 규칙). 로거 `trader.scan`.

## 알려진 차이 (옛 저장소 대비)

1. `ThemeCfg.interval_s` 는 없다. 주기는 `schedule.yaml theme.interval_s`.
2. `mature` 는 `state` 대신 `close_table(market, since, until)` 콜러블을 주입받는다(피처 스토어 종가는 M8). None 이면 경고하고 0 을 돌려준다. 수익 창 계산은 `memory.outcomes.window_return` 대신 이 모듈의 private 구현(pandas import 없음).
3. `compute` 의 `vol_z` 는 `{}`(피처 스토어 `vol_z_20` 은 M8 이후), `state` 인자는 없다. 섹터 맵은 `sector_of` 로 주입하고, 없으면 `ingest.api.load_sectors()` 를 시도하다 실패하면 키워드 테마만 쓴다.
4. 이 단계에는 잡·`ScanPort` 구현이 없다(이후 태스크가 `theme.py` 함수를 연결한다).
5. discovery(scout 후보 검증·병합·`event_watch` 입력)는 M7 로 이연 — 옛 test_events 의 discovery 관련 단언은 이식하지 않았다. 옛 게이트 테스트(shock 종목 테마 안전장치)는 Task 12(`theme_of`/spot 게이트 연결) 몫이다. 사건 감지는 데몬 직접 호출 대신 `news.post_ingest` 훅으로 돈다.
6. 알림 순서는 옛 코드 그대로 "알림 완료 표시(`new_for_alert`) 후 전송"이다. 전송이 실패해도(로그 debug) 그 사건은 그날 다시 알리지 않는다. `notifier=None` 이면 전송만 건너뛰고 `alerted` 는 채워진다.
