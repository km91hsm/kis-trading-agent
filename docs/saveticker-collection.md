# SaveTicker 수집 (Chrome 확장)

뉴스 전체를 수집해 속보/SAVE PICK/로이터 등 출처와 카테고리로 분류하고, SAVE 데일리 리포트 PDF 원본과 추출 텍스트를 따로 보관한다. 커뮤니티는 지수/상승/하락/종목별 커뮤니티 TOP5만 수집한다. 사용자 게시글·댓글은 수집하지 않는다. 증시 거래대금/상승/하락, 캘린더 월별 일정도 저장한다. 거래·LLM 실행은 하지 않는다.

수집은 **일반 Chrome 의 확장(`extensions/saveticker/`) + Native host(`trader saveticker native-host`)** 한 가지 방식뿐이다. 사용자가 Chrome 에서 직접 로그인하고, 확장이 로그인된 화면·응답을 읽어 Native host 로 보내면 Native host 가 검증해 저장한다. 계정 파일·Playwright Chromium 수집기(옛 방식)는 2026-11 에 제거했다. 대신 확장 팝업의 **재확인 시 계속 복구** 를 켜면 재확인·로그아웃으로 막혀도 수집을 끄지 않고 1→2→4→8→15분(이후 15분) 간격으로 전용 탭 새로고침을 반복하고, 로그아웃이면 `trader saveticker login set` 으로 저장한 이메일·비밀번호(`secrets/.env` 의 `SAVETICKER_EMAIL`·`SAVETICKER_PASSWORD`, 0600)로 사이트의 이메일 로그인 대화상자를 채운다(확장 1.5.0). 보안 확인 화면은 조작하지 않는다. 확장의 동작·권한·버전별 변경은 `extensions/saveticker/README.md` 가 정본이다.

## 설치

웹 UI: 확장이 미등록이면 상단 첫 안내 **설치하기**, 언제든 시스템 › 기능 › 데이터 수집 **설치/갱신**(이 PC 직접 접속 한정, `POST /api/system/saveticker/install` = 아래 `install`). 계속 복구용 로그인 정보는 시스템 › 관리 › 연결 키 「세이브티커」(`SAVETICKER_EMAIL`·`SAVETICKER_PASSWORD`).

```text
uv run trader saveticker extension install     # 확장 복사 + Native host 등록(재실행 가능)
uv run trader saveticker extension status      # 등록·등록 JSON path·확장 버전·연결·작업별 상태
uv run trader saveticker extension uninstall   # 확장 팝업에서 중지한 뒤 등록 해제
uv run trader saveticker login set|status|clear # 계속 복구용 이메일 로그인 정보 저장·저장 여부·삭제(값은 출력 안 함)
```

`install` 은 경로 인자가 없다. 패키지는 고정 `<데이터 루트>/saveticker-extension/`(번들은 macOS `~/Library/Application Support/Trader/saveticker-extension`, Windows `%LOCALAPPDATA%\Trader\saveticker-extension`)이고, 번들의 확장을 그 안 `extension/` 으로 복사한 뒤 **설치된 앱의 콘솔 `trader` 를 Native host 로 직접 등록**한다(macOS 는 `env TRADER_HOME=… SAVETICKER_NATIVE_ROOT=… <trader>` 래퍼 `native-host`, Windows 는 등록 JSON `path` = 설치 폴더 `trader.exe` + HKCU 레지스트리). 앱 복사본(옛 `native-runtime/`)은 만들지 않는다. Gatekeeper 번역 경로(`AppTranslocation`)나 마운트한 디스크 이미지(`/Volumes/…`)에서 실행 중이면 `app_translocated` 로 거부한다 — 앱을 `/Applications` 로 옮긴 뒤 다시 실행한다.

다시 실행해도 된다: 같은 이름(`com.trader.saveticker`)·같은 확장 ID 의 기존 등록(옛 `prepare` 패키지 등록 포함)은 교체하고, 다른 소유자의 등록이면 `registration_exists` 로 거부한다. 결과의 `next_steps` 대로 `chrome://extensions` → 개발자 모드 → (다른 경로로 로드된 옛 확장이 있으면 먼저 삭제) → 「압축해제된 확장 프로그램 로드」 → `<패키지>/extension` → 확장 팝업에서 시작한다. 확장 팝업에서 시작·중지·재개한다.

앱을 업데이트하면 데몬 잡 `saveticker` 의 첫 틱이 설치된 `extension/manifest.json` 버전을 번들과 비교해 다르면 복사본을 교체하고 「Chrome 확장 프로그램 화면에서 Trader 수집 확장을 새로고침하세요」 텔레그램 알림을 한 번 보낸다. 등록이 현재 실행파일을 가리키지 않으면(앱 위치 변경) 그것도 다시 맞춘다. 업데이트 전에는 확장 팝업에서 수집을 중지해 두는 것이 안전하다. 리포트 PDF 는 Chrome 기본 다운로드 폴더(`~/Downloads/SaveTicker`)를 거치므로 Chrome 다운로드 폴더를 바꾸지 않는다. Chrome 과 컴퓨터가 켜져 있어야 수집하며, 세션 만료·사이트 보안 확인은 사용자가 Chrome 에서 직접 마친 뒤 팝업의 재개를 눌러야 한다(우회하지 않는다).

## 조회

```text
uv run trader saveticker status
uv run trader saveticker query --ticker NVDA --limit 50
uv run trader saveticker query --asof 2026-10-25T00:00:00+00:00
uv run trader saveticker export
uv run trader saveticker import-capture <캡처 JSON>
```

`status` 는 스트림별 마지막 시도·성공 시각과 경과 초, Native host 의 마지막 상태(`runner.json`)를 보여준다. 캘린더 범위는 KST 지난달부터 3개월 뒤까지(양 끝 포함)이며 Native host 가 확장에 넘기는 수집 계획(`native.build_plan`)이 달이 바뀌면 굴린다.

## 데몬 감시

데몬 잡 `saveticker`(60초, 프로세스를 띄우지 않음)는 확장이 등록돼 있을 때 Native host 기록(`runtime/runner*.json`, Native host 생존)과 `data/status.json` 의 유효 갱신 시각을 보고 장애·복구를 판정해 텔레그램으로 한 번씩 알린다(`automation.monitor`). Native host 종료는 즉시, 유효 갱신 120초 지연·파일 읽기 실패는 연속 2회 확인 뒤 장애로 본다. 같은 장애는 중복 알리지 않는다. 운영일 1회 보관 기간 정리(`retention_days`)도 이 잡이 한다. 웹 「다시 시작」은 요청만 남기고, 데몬은 「Chrome 확장 팝업에서 재개」 안내만 보낸다. `sources.yaml` 의 `saveticker.collect: false` 면 감시·정리를 하지 않는다(쌓인 데이터는 계속 읽는다).

운영 점검(`trader status`·웹 대시보드)은 확장이 등록됐을 때 연결 끊김(`saveticker.disconnected`)과 로그인·보안 확인으로 인한 멈춤(`saveticker.stopped`)을 warning 으로 올린다.

## 파일

기준 폴더는 `config/sources.yaml` 의 `saveticker.dir`(기본 `$TRADER_HOME/state/external/saveticker`) 아래 `data/`다 — 리더가 읽는 곳과 같다. 상대 경로는 프로젝트 루트 기준이다. `sources.yaml saveticker.retention_days`(30) 지난 변경 이력·화면 캡처(스트림별 마지막 제외)·날짜 폴더·PDF 원본은 데몬이 운영일 1회 지운다(추출 텍스트·manifest 는 남김). 수집 결과는 `data/`, 로그·실행 상태·잠금 파일은 형제 폴더 `runtime/`로 분리한다.

```text
feed.sqlite3                  변경 이력 원본(WAL)
2026-10-25/news.jsonl          UTC 수신일 기준 뉴스 변경분
2026-10-25/community.jsonl     지수/상승/하락/TOP5 변경분
2026-10-25/market.jsonl        순위·시세 변경분
2026-10-25/calendar.jsonl      일정·발표값 변경분
latest/<stream>.json          스트림별 최근 유효 캡처
status.json                   시도·유효 수집 시각, 상태, 처리 소요·목표 주기
../runtime/bridge-config.json  확장 등록 기록(`extension install` 이 만든다)
../runtime/bridge.json        Native host 하트비트(pid·상태·사유·기록 시각)
../runtime/bridge-host.lock   Native host 중복 실행 잠금
../runtime/runner.json        Native host 마지막 상태
../runtime/runner-<lane>.json 작업(news·community·market·background)별 상태
../runtime/alert-monitor.json 데몬 장애 감시 기록
attachments/<sha256>.pdf       다운로드 PDF 원본
attachments/<sha256>.txt       PDF에서 추출한 텍스트
attachments/report-<id>.json   다운로드 확인 시각과 파일 manifest
```

JSONL은 한 줄이 변경 버전 하나이며 `{revision, stream, received_at, item}`이다. `view`에는 출처·카테고리·탭·조회 월을 남긴다. `item`에는 `source_group, categories, is_breaking, report_date, attachment, id, kind, title, body, tickers, published_at, scheduled_at, scheduled_date, price, change_pct, turnover, rank, forecast, previous, actual, views, likes, comment_count, parent_id, detail_status, basis`가 들어간다. 해당 없는 값은 null/빈 문자열이다.

수집 시각은 UTC, 화면 시간대는 Asia/Seoul로 고정한다. 상대시각만 있는 목록은 `published_at=null`이며 추정한 절대시각을 만들지 않는다. 캘린더 `scheduled_at`은 미래여도 유효한 예정 정보다. 종일·시각 미정 일정은 `scheduled_at=null`과 `scheduled_date`로 보존한다. 발표값의 알려진 시각은 `received_at`이며 일정 시각으로 과거에 알았다고 처리하면 안 된다.

스트림 내 같은 행과 상대시각만 달라진 행은 중복 버전을 만들지 않는다. 뉴스는 조회수·좋아요·댓글 수만 바뀐 행도 새 버전이 아니다(하루 1만 줄 중 대부분이 조회수 변경이었다). 수정·순위 변화와 커뮤니티·순위 화면의 조회수·좋아요 변화는 새 버전이다. 사이트가 고유 ID를 노출하지 않는 화면에서는 제목+출처 의 해시를 사용하므로 동일 제목의 기사가 겹칠 가능성이 있다. 원문 고유 ID가 확인되면 우선하도록 확장할 수 있다.

SQLite 커밋 후 JSONL과 최신 파일을 원자적으로 교체한다. 파일 생성 중 중단돼도 `export`로 원본에서 복구한다. 오류/로그인 만료/빈 응답이 나면 기존 유효 최신 파일을 지우지 않으며 반드시 `status.json`의 시각·상태를 함께 읽는다. `query --asof`는 수신 시각과 발행 시각이 asof 이하인 버전만 반환한다. 다른 탭의 본문 대기 행이 기존 본문을 가리지 않도록 마지막 확인된 본문 버전을 원래 수신 시각과 함께 우선한다. 순위·캘린더는 그 시점 마지막 유효 화면에 실제 있는 항목만 반환한다. 기존 `00_inputs.json`을 자동 변경하지 않는다.

## 주기와 전체 수집의 의미

수집 주기·작업 한도(스크롤·본문·PDF 건수)는 Native host 의 수집 계획이 정한다(전체 뉴스·실시간 순위 30초, 정규장 순위·캘린더 300초, 리포트 900초 목표, 작업당 스크롤 2회·본문 2건·리포트 2건). 실제 지연은 보장이 아니며 `status.json` 의 시각과 `trader saveticker status` 의 `age_s` 로 확인한다. 뉴스 목록은 가상 스크롤 범위 안에서 누적하며, 본문 대기·한도 도달·끝 미확인은 `partial` 로 표시한다. 전체 사이트의 과거 뉴스를 다 받았다고 간주하지 않는다. 트렌드는 2026-10-26 사용자 요청으로 수집·장애 감시에서 제외했고 과거 저장 이력은 유지한다.

PDF 는 확장이 Chrome 다운로드로 받고 Native host 가 일회 ticket·크기·SHA256·형식을 검증해 보관한다. 추출 텍스트가 없는 PDF 도 원본을 보관하고 `pdf_only` 로 구분한다. 보고서 날짜는 화면에 적힌 일자를 `report_date` 로 보존하며 발행 시각을 임의로 만들지 않는다.

## 2026-10-26 JSON 응답 전환

뉴스·커뮤니티 순위·증시·캘린더는 정상 브라우저 UI가 수신한 허용 JSON 응답에서 추출한다. 독립 HTTP 요청이나 사용자 글·댓글 API는 사용하지 않는다. 로그인·탭 선택·스크롤·기사 열기는 UI로 수행하며, SAVE 리포트 목록/PDF 원본 다운로드는 기존 UI 경로를 유지한다.

뉴스 목록 content는 본문으로 저장하지 않는다. 기사를 열 때 도착한 상세 JSON에서 텍스트 블록을 펼치고 한국어 번역이 제공되면 한국어 제목·본문을 선택한다. 한도 내 미처리 기사는 pending으로 남긴다. 응답은 현재 수집 회차에서만 사용하며 조회 실패나 차단을 성공으로 표시하지 않는다. 시세 문자열은 JSON의 숫자를 그대로 보존하며 통화 기호·퍼센트 기호를 덧붙이지 않는다.

## 추가로 권하는 데이터

1. 발표 직전 예상치와 발표 후 실제값·수정치: 서프라이즈와 당시 알려진 값을 구분.
2. 순위 진입/이탈과 변화 속도: 상승률 숫자보다 지속성과 거래대금 동반 여부 확인.
3. 동일 기사 묶음·정정 이력·원문 출처: 재전송된 속보를 여러 호재로 세지 않기.
4. 지연·누락·로그인 만료 지표: 데이터가 없다는 것과 수집에 실패했다는 것을 구분.
5. 공식 원천 교차 확인: KIS 시세·호가·체결(기존 수집기), SEC/DART 공시, FRED 경제지표 빈티지. 새로운 유료 소스는 추가하지 않는다.

공식 참고: [Chrome Native Messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging), [FRED observations/vintage_dates](https://fred.stlouisfed.org/docs/api/fred/series_observations.html), [SEC API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces).
