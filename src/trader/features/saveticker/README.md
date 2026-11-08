# saveticker

## 책임

- 세이브티커(saveticker.com) 로컬 수집을 소유한다. 수집은 일반 Chrome 의 확장(`extensions/saveticker/`)이 사용자가 직접 로그인한 화면·응답을 읽어 Native host(`native.py`, 숨김 명령 `native-host`)로 보내고, Native host 가 검증해 수집 폴더(`sources.yaml` 의 `saveticker.dir`, 기본 `state/external/saveticker`)의 `data/`(SQLite `feed.sqlite3` 원본 + 날짜별 JSONL·`latest/`·`attachments/`)·`runtime/` 에 쓴다. 뉴스·리포트 PDF·커뮤니티 TOP5/지수/상승·하락·증시 순위·캘린더.
- 수집 계획(화면 목록·주기·한도)은 `views.py`(`View`·`build_views`·`lane_for`)와 `native.build_plan` 이 정한다. 확장 설치·Native host 등록은 `bridge_install.py`(`extension install|uninstall|status` — 고정 패키지 `<데이터 루트>/saveticker-extension/`, 설치된 앱의 콘솔 `trader` 를 직접 등록, 우리 등록만 멱등 교체). 웹은 `POST /system/saveticker/install`(토큰 + 같은 출처 + 이 PC 직접 접속)로 같은 `install()` 을 바로 부르고, `GET /system/saveticker` 의 `install`(경로는 직접 접속일 때만)·`onboarding`(control 플래그 `saveticker.onboarding_dismissed`, `POST /system/saveticker/onboarding`)으로 첫 안내 배너를 띄운다. 데몬 잡 첫 틱이 `bridge_install.sync` 로 앱 업데이트 뒤 확장 복사본·등록을 맞추고 확장이 바뀌었으면 새로고침 안내를 한 번 보낸다.
- 계속 복구용 로그인 정보는 `credentials.py`(`secrets/.env` 의 `SAVETICKER_EMAIL`·`SAVETICKER_PASSWORD` — CLI `login set|status|clear` 또는 연결 키 설정 화면)이고 Native host `credentials` 메시지로만 확장에 건넨다.
- 계정 파일·Playwright Chromium 수집기(`run`·`service`·`monitor`·`install-browser`, launchd plist)는 2026-11 에 제거했다.
- 리더(`reader.py`)가 같은 폴더를 읽어 뉴스 항목·종목 TOP5·지수·순위·캘린더·리포트 발췌를 돌려준다(`snapshot`, `upcoming`, `for_ticker`, `read_news_items`).
- 데몬 잡 `saveticker`(60초, heavy 밖)는 아무 프로세스도 띄우지 않는다. 확장이 등록돼 있으면(`automation.registered`) 수집기 감시(`automation.monitor` — Native host 생존·`runner*.json`·`status.json` 갱신 지연)가 판정한 장애·복구를 텔레그램(`messages.saveticker_health`)으로 한 번씩 보내고, 운영일 1회 `Store.prune(retention_days)` 를 한다. teardown 없음.

## 웹 (M11, `routes.py`)

- `GET /system/saveticker`(옛 `system.saveticker_status`, 토큰 없음): `configured`(확장 Native host 등록 여부) + `web_status.read_status`(Native host 연결 상태 `service`·`blocked`·`reason_text`·작업별 `lanes`·`health`·스트림별 `streams`·`summary`·`recovery{state,text}`). 계정·값은 돌려주지 않는다. Native host 가 2분 안에 기록하지 않았거나 잠금을 쥐지 않으면 `waiting_chrome`.
- `POST /system/saveticker/restart`(토큰 + 같은 출처, T11-R2): control `saveticker_restart_requested = {at, actor}` 를 세우고 `control_log`(channel web)에 남길 뿐 수집 파일은 건드리지 않는다. 응답 `note` 로 Chrome 확장 팝업 재개를 안내한다. 데몬 잡 `watch` 가 다음 틱에 `consume_restart` 로 소비해 같은 안내를 텔레그램으로 보내고 플래그를 지운다(전송 실패도 로그 뒤 지운다 — 수집이 꺼져 있으면 남아 있다가 켜진 뒤 소비). 상수는 `restart.py`(데몬 잡이 라우터 모듈을 import 하지 않게).
- 제어 네임스페이스 `saveticker_restart_requested`, nav `system/saveticker`(저장소), SSE 키 없음.
- 옛 재시작은 `require_direct_local`(이 PC 직접 접속)이었으나 이제 토큰 + 같은 출처다(웹이 직접 실행하지 않고 요청만 남기므로).

## 공개면 (`api.py`)

`SaveTickerCfg`, `load_cfg`, `DEFAULT_DIR`, `resolve_dir`, `home`, `available`, `published_at_of`, `markets_of`, `read_news_items`, `report_excerpt`, `upcoming`, `snapshot`, `for_ticker`, `JSONL_DAYS`, `NEWS_STREAMS`, `SCHEMA_VERSION`. 다른 기능(ingest)은 이 파일만 import 한다.

CLI: `trader saveticker status | import-capture | query | export | extension install|uninstall|status` (숨김 `native-host ORIGIN`).

## 규약

- 로그인·보안 확인·CAPTCHA 는 우회하지 않는다. 확장이 멈추고 사용자가 Chrome 에서 직접 마친 뒤 팝업에서 재개한다(Native host 상태 `waiting_verification`·`auth_required` → 웹 `blocked`). `runner*.json` 에는 상태·사유 코드만 남기고 메시지·계정은 남기지 않는다.
- 리더 룩어헤드·신선도: `observed_at`/`received_at` 이 asof 이하인 것만. latest 화면이 asof 보다 1시간(리포트 하루, 캘린더 2주) 넘게 오래되면 수집기 정지로 보고 빼며 `snapshot.stale` 에 스트림 이름을 남긴다. 커뮤니티 트렌드(2026-10-26 수집 중단)는 읽지 않는다. 리포트 발췌는 `data/` 밖 경로를 거부한다(None).
- 뉴스는 조회수·좋아요·댓글 수만 바뀐 행을 새 버전으로 쓰지 않는다(`store._stable`).
- 코드는 `kernel/` 만 import 한다(3층 규칙). 외부 사이트 원문은 fixture 로 커밋하지 않는다(합성 데이터).

## 테스트

`tests/features/saveticker/` — 리더(`test_reader`), CLI(`test_cli`), 데몬 잡(`test_job`), feature·공개면(`test_feature`, `test_public_api`), Native host·확장 계약 각 모듈. `test_*_browser.py` 는 확장 JS 를 실제 Chromium(dev 의존성 playwright)으로 시험하며 Chromium 이 없으면 skip 한다. 합성 fixture 는 `fixtures/saveticker/`.

## 알려진 차이 (옛 저장소 대비)

1. 설정 `news.saveticker_dir|saveticker_collect|saveticker_retention_days` → `sources.yaml` 의 `saveticker` 섹션(`dir`, `collect`, `retention_days`).
2. `to_store_item`·`ingest_news`(뉴스 저장소 적재)는 ingest 기능으로 옮겼다. 리더는 `report_excerpt`(첨부 없음·경로 밖이면 None, `snapshot.report.excerpt` 는 빈 문자열)만 공개한다.
3. 네이티브 호스트 origin argv 의 `__main__` 우회(`test_native_origin_routes_before_cli`)는 후속 마일스톤으로 미뤘다. `native-host ORIGIN` 숨김 명령은 있다.
4. 데몬 상태(마지막 정리일 `pruned`)는 `DaemonDeps` 필드가 아니라 `job._STATE`(프로세스 전역)에 둔다.
5. 리더 로거 이름은 `trader.features.saveticker`(옛 `trader.sources`).
