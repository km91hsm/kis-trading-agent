# saveticker

## 책임

- 세이브티커(saveticker.com) 로컬 수집기를 소유한다. `secrets/saveticker.env` 계정으로 일반 창 Chromium(Playwright)에서 로그인해 뉴스·리포트 PDF·커뮤니티 TOP5/지수/상승·하락·증시 순위·캘린더를 읽어 수집 폴더(`sources.yaml` 의 `saveticker.dir`, 기본 `state/external/saveticker`)의 `data/`(SQLite `feed.sqlite3` 원본 + 날짜별 JSONL·`latest/`·`attachments/`)·`runtime/` 에 쓴다.
- 구조: `run`(감독) → 작업 4개(news·community·market·background) 각자 브라우저, `service` 가 감독을 띄우고 일시 오류만 최대 3회 재기동(`service.json`), `monitor` 가 러너·스트림 신선도를 보고 장애·복구를 한 번씩 알린다.
- 리더(`reader.py`)가 같은 폴더를 읽어 뉴스 항목·종목 TOP5·지수·순위·캘린더·리포트 발췌를 돌려준다(`snapshot`, `upcoming`, `for_ticker`, `read_news_items`).
- 데몬 잡 `saveticker`(60초, heavy 밖)가 `automation.should_start` 를 따라 `trader saveticker service` 를 분리 프로세스로 띄우고, 장애 감시를 텔레그램(`messages.saveticker_health`)으로 돌리고, 운영일 1회 `Store.prune(retention_days)` 를 한다. 종료 때 `procs.stop_tree` 로 정리한다.

## 웹 (M11, `routes.py`)

- `GET /system/saveticker`(옛 `system.saveticker_status`, 토큰 없음): `configured`(계정 파일 유무, 확장 모드는 계정 불필요) + `automation.status`. 계정·값은 돌려주지 않는다.
- `POST /system/saveticker/restart`(토큰 + 같은 출처, T11-R2): control `saveticker_restart_requested = {at, actor}` 를 세우고 `control_log`(channel web)에 남길 뿐 수집 프로세스·파일은 건드리지 않는다. 확장 모드면 응답 `note` 로 Chrome 확장 팝업 재개를 안내한다. 데몬 잡 `watch` 가 다음 틱에 `consume_restart` 로 소비한다: 확장 백엔드는 정지 기록을 지우지 않고 안내 알림만(옛 409 "popup instead of legacy reset"), 그 외는 `automation.clear_blocks` 후 같은 틱에 띄우기 판정. 어느 쪽이든 플래그를 지운다(수집이 꺼져 있으면 남아 있다가 켜진 뒤 소비).
- 제어 네임스페이스 `saveticker_restart_requested`, nav `system/saveticker`(저장소).
- 옛 재시작은 `require_direct_local`(이 PC 직접 접속)이었으나 이제 토큰 + 같은 출처다(웹이 직접 실행하지 않고 요청만 남기므로).

## 공개면 (`api.py`)

`SaveTickerCfg`, `load_cfg`, `DEFAULT_DIR`, `resolve_dir`, `home`, `credentials_path`, `available`, `published_at_of`, `markets_of`, `read_news_items`, `report_excerpt`, `upcoming`, `snapshot`, `for_ticker`, `JSONL_DAYS`, `NEWS_STREAMS`, `SCHEMA_VERSION`. 다른 기능(ingest)은 이 파일만 import 한다.

CLI: `trader saveticker status | import-capture | query | export | run | install-browser | service | monitor | extension prepare|install-host|uninstall-host|status` (숨김 `native-host ORIGIN`).

## 규약

- headless 403·보안 확인·CAPTCHA 는 우회하지 않고 실패로 기록한다. `runner*.json` 에는 단계·오류 타입만 남기고 메시지·계정은 남기지 않는다.
- 작업 4개는 각자 브라우저를 쓴다. 작업 하나의 `AccessDenied` 는 그 작업만 6시간 쉬고(`automation.classify_exit` → `run --skip-lanes`), 세션 상실·전 작업 거부만 전체 정지이며 전체 정지도 6시간마다 1회 재시도한다.
- 사이트 재확인 화면(`browser.VerificationRequired`)을 만나면 폼을 채우지 않고 정지 사유 `verification` 으로 멈춘다. 작업들은 `login.lock` 으로 한 번에 하나씩 로그인한다(네 창 동시 로그인이 재확인을 부름). 같은 폴더의 `service.lock` 은 공유하며, 다른 프로세스(launchd 등)가 쥐거나 이 PC 에 다른 폴더의 서비스가 돌면(`service_elsewhere`) 데몬 잡은 띄우지 않는다.
- `automation.should_start`: 계정 파일 존재만 확인(값은 수집 프로세스만 읽는다). 인증 실패 `blocked` 는 계정 파일이 바뀐 뒤에만, 재기동 한도·Chromium 설치 실패는 1시간 뒤.
- 리더 룩어헤드·신선도: `observed_at`/`received_at` 이 asof 이하인 것만. latest 화면이 asof 보다 1시간(리포트 하루, 캘린더 2주) 넘게 오래되면 수집기 정지로 보고 빼며 `snapshot.stale` 에 스트림 이름을 남긴다. 커뮤니티 트렌드(2026-10-26 수집 중단)는 읽지 않는다. 리포트 발췌는 `data/` 밖 경로를 거부한다(None).
- 뉴스는 조회수·좋아요·댓글 수만 바뀐 행을 새 버전으로 쓰지 않는다(`store._stable`).
- 코드는 `kernel/` 만 import 한다(3층 규칙). 외부 사이트 원문은 fixture 로 커밋하지 않는다(합성 데이터).

## 테스트

`tests/features/saveticker/` — 리더(`test_reader`), CLI(`test_cli`), 데몬 잡(`test_job`), feature·공개면(`test_feature`, `test_public_api`), 수집기 각 모듈. 합성 fixture 는 `fixtures/saveticker/`.

## 알려진 차이 (옛 저장소 대비)

1. 설정 `news.saveticker_dir|saveticker_collect|saveticker_retention_days` → `sources.yaml` 의 `saveticker` 섹션(`dir`, `collect`, `retention_days`).
2. `to_store_item`·`ingest_news`(뉴스 저장소 적재)는 ingest 기능으로 옮겼다. 리더는 `report_excerpt`(첨부 없음·경로 밖이면 None, `snapshot.report.excerpt` 는 빈 문자열)만 공개한다.
3. 네이티브 호스트 origin argv 의 `__main__` 우회(`test_native_origin_routes_before_cli`)는 후속 마일스톤으로 미뤘다. `native-host ORIGIN` 숨김 명령은 있다.
4. 데몬 상태(`saveticker_proc|home|pruned`)는 `DaemonDeps` 필드가 아니라 `job._STATE`(프로세스 전역)에 둔다.
5. 리더 로거 이름은 `trader.features.saveticker`(옛 `trader.sources`).
