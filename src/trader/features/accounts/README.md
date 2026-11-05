# accounts

## 책임

- `config/accounts.yaml` 의 계좌 목록(모델·검증·cano 자리표시자 채움·markets 기본 `["KR","US"]`)과 계좌 모드(`off|approve|auto`)를 소유한다.
- 실전 계좌를 `auto` 로 두려면 LIVE 확인이 선행돼야 한다(`LIVE_CONFIRM = "LIVE"`). 모드·확인 상태는 원장 테이블 `accounts`·`account_modes`(마이그레이션 v2)에 둔다. 전역 일시정지는 커널 control 플래그 `paused`(`control_namespaces=("paused",)`).
- 계좌별 KIS 키(`KIS_PAPER_*`/`KIS_REAL_*` 접두 시크릿)로 시세·브로커를 조립한다(`brokers.py`). `secrets/.env` 가져오기·카탈로그·갱신은 `secrets_io.py`, 계좌 추가·삭제는 `accounts_io.py`.
- 텔레그램 명령 `/pause /resume /mode` 와 `/status` 의 "계좌" 절을 `kernel/notify` 디스패처에 등록한다.

## 공개면 (`api.py`)

`LIVE_CONFIRM`, `Account`, `ConfirmationRequired`, `Mode`, `account_mode`, `build_brokers`, `build_market`, `effective_mode`, `get_account_mode`, `is_paused`, `load_accounts`, `market_creds`, `split_accounts`, `to_broker_account`. 다른 기능은 이 파일만 import 한다.

CLI: `trader accounts show | mode | pause | resume | ping | add | remove | secrets import`.

웹(`routes.py`, M11): `GET /accounts`(행: id·env·lane·markets·currency·mode·mode_note·mode_source·paused·nav·nav_at·cash·cash_at·usd_nav·day_pnl·positions_count·pending_approvals — 금액 `str(Decimal)`, `*_at` 은 `account_nav.created_at` UTC, 스냅샷 없으면 null)·`GET /accounts/overview`(KRW·USD 합계 + `quotes.api.fx_rate` 가 있으면 원화 환산, 파생 제외)·`PATCH /accounts/{id}`(실전은 `confirm`=계좌 id; 실전 auto 는 본문 `confirm` 에 계좌 id 를 다시 적으면 웹이 LIVE 확인 기록을 남긴다 — `live_confirmed_json` 에 confirm·at·channel `web`·actor(토큰 지문), `control_log` `account_mode:<id>` 도 같은 channel·actor. 이미 기록이 있으면 다시 쓰지 않는다. 사용자 결정 T6-R1 변경 2026-11-05)·`POST /accounts`·`DELETE /accounts/{id}`(이 PC 직접 접속만)·`GET /settings`·`PATCH /settings/paused`(실전 auto 계좌가 있으면 재개에 그 id 중 하나를 `confirm`)·`GET|POST /settings/secrets`(직접 접속만 — GET 도 토큰을 요구한다: 옛 `POST /control/secrets/status` 는 직접 접속만 봤다. M11b 계약: 연결 키 화면은 토큰을 붙여 부른다). 조립은 `overview.py`(`lane_of`: asset fo → fo, `settings.scalp.accounts` 덕 타이핑 → scalp, 그 밖 spot). SSE 키 `accounts`(모드 지문+paused)·`positions`(보유·NAV 최근 사흘)·`approvals`(pending 수+상태 지문), 메뉴 `accounts/overview "통합"`. 알려진 차이: `settings.scalp.accounts` 키는 아직 없어 `lane_of` 의 scalp 규칙은 맞는 계좌가 없다(T7-R2 — 단타 설정에 계좌 키가 생기면 통일), 파생 NAV 는 통합 현황에 없다(`account_nav` 만 읽음, 파생은 `/fo/summary`, T6-R2).

## 규약

- 같은 값으로 모드를 다시 설정하면 쓰기를 하지 않는다(`set_account_mode`; 실전 auto 를 같은 confirm 으로 재확인하면 확인 시각 `at` 이 바뀌어 다시 쓴다). `effective_mode` 는 실전 계좌가 `auto` 인데 LIVE 미확인이면 **`off`** 로 취급하고(`LIVE_UNCONFIRMED_NOTE`) 설명을 붙인다. 실전 파생 계좌는 주문이 없다(`REAL_FO_OFF_NOTE`).
- 키 규칙: 공용 `KIS_PAPER_*`/`KIS_REAL_*`, 계좌 전용 `KIS_<ID 대문자, 영숫자 외는 _>_*`(`account_prefix`; 예 `real-2` → `KIS_REAL_2_`). 계좌의 `credentials` 로 접두사를 직접 지정할 수 있다(`KIS_…_` 형식). id 에서 유도한 접두사가 예약 접두사 `KIS_PAPER_`/`KIS_REAL_` 와 같은 계좌 id 는 `accounts add` 가 거부한다(`accounts_io._check_spec`). 전용 접두사에 `APP_KEY` 가 없으면 env 공용 키로 폴백한다.
- 레인 포트(`ports.py` `AccountsPortImpl`, `Boot.ports["accounts"]`)는 현물 레인용이라 **현물 계좌만** 다룬다: 받은 목록을 `split_accounts(...)[0]` 로 걸러 `accounts()`·`brokers()`·`market_client()` 가 같은 현물 목록에서 나온다. `policy()` 는 `account_mode`(실전 파생 항상 off + 실전 auto LIVE 미확인 off)다.
- fo 레인용 접근자(M10): `fo_accounts()` 는 `split_accounts(...)[1]`(파생 계좌), `fo_brokers()` 는 `settings.fo.product_cfg()` 를 덕 타이핑으로 얻어 `build_brokers(..., fo_product=)` 로 조립한 파생 브로커 dict(`settings.fo` 가 없으면 빈 dict + 경고; accounts 는 fo 를 import 하지 않는다). `accounts()`·`brokers()`·`market_client()` 는 현물 전용 그대로이고 `brokers()` 는 **list** 를 돌려준다(커널 `AccountsPort.brokers()` Protocol 도 `list[Any]`). fo 가 쓰는 나머지는 `market_client()`·`policy()`·`paused()`·`holiday_fn()`.
- 시세 키(`market_creds`) 폴백 순서: 공용 실전 키 → 첫 실전 현물 계좌의 전용 키 → 공용(없음). 파생 계좌 키는 쓰지 않는다.
- 같은 키를 여러 계좌가 쓰면 경고(주식 WARNING, 파생 INFO). 키가 없는 계좌, `fo_product` 가 없는 파생 계좌는 경고 후 건너뛴다.
- 시크릿 값은 로그·stdout·예외 메시지에 넣지 않는다(이름과 '설정됨' 여부만). 로거는 `trader.config`(옛 호환)·`trader.accounts`.
- 코드는 `kernel/` 만 import 한다(3층 규칙).

## 테스트

`tests/features/accounts/` — 모드·`accounts_io`·`secrets_io`·다중 계좌 키·CLI·텔레그램 명령·웹 자원(`test_routes.py`).

## 알려진 차이 (옛 저장소 대비)

1. `control.mode:*`·`live_confirmed:*` 플래그 → `account_modes` 테이블(스펙 §2.4).
2. CLI: `control` → `accounts`, `kis ping` → `accounts ping`, `secrets import` → `accounts secrets import`. `accounts remove` 는 성공 시 `삭제: …` 를 출력한다(옛 출력에 없던 줄).
3. `/watch`·watchlist 는 M7(scan).
4. `/approve`·`/reject`·`/run`·`/kill`·`/daily`·`/fo` 는 각 M(M4·M7·M6·M8·M10). `/status` 의 데몬·국면·스캔·미승인 줄도 해당 M 이 `status_sections` 로 더한다.
5. `exec-key list|release` 는 M4.
6. `accounts_io.blockers` 는 테이블이 없으면 그 사유를 생략한다. 끝난 주문 상태 목록은 커널 `execution.types.dead_status_sql` 을 쓴다(M9). `test_accounts_io.py` 의 `db` fixture 는 M4 전까지 최소 스탠드인 테이블을 CREATE 한다.
7. 체결통보 리스너(`_notice_listeners`)는 M5.
8. `ensure_web_token`(`secrets_io`)은 커널 `config.secrets.ensure_web_token` 으로 승격했고(`.env` 편집 helper 포함, `secrets_io` 는 다시 내보냄), `trader app` 이 첫 실행에 호출한다. 연결 키 웹 라우트는 `GET|POST /settings/secrets`(옛 `POST /control/secrets/status` → GET). 계좌 삭제는 옛 `POST /control/accounts/remove` → `DELETE /accounts/{id}`, 모드는 옛 `POST /control/mode`(실전 auto confirm=LIVE) → `PATCH /accounts/{id}`(confirm=계좌 id + 기존 LIVE 기록).
9. `parse_command("/foo")` 는 이름 `foo` 를 돌려준다(거부는 디스패처의 몫).
10. `REAL_FO_OFF_NOTE` 문구를 옛 "Phase 26 범위 밖" 에서 "실전 파생 계좌는 주문 없음(파생 레인 범위 밖)" 으로 바꿨다.
11. 옛 `SCALP_EXIT` 표·`DecisionBrief` 는 커널 `notify.messages` 에 이식하지 않았다 — `SCALP_EXIT` 는 M9 에서 scalp `messages.py` 로 이식됐다(`carry_over` 추가), `DecisionBrief` 는 spot 몫.
12. `/status` 는 M9 에서 절별 격리를 추가했다(실패 절은 `- {title}: 조회 실패(...)` 한 줄로 남고 나머지 절은 그대로 보인다).
13. `notify.enabled=false` 와 토큰 없음이 `telegram poll|loop` 에서 같은 토큰 오류 문구로 나온다(원인 구분 없음).
14. 명령별 echo 는 `name arg` 한 줄로 통일했다. `/mode <id> fast` 는 옛 코드처럼 "거부: …".
15. `_COMMAND_RE` 는 `[a-z_]+` 라 숫자가 든 명령 이름은 쓸 수 없다. 핸들러는 `load_accounts()` 를 시크릿 없이 호출한다.
16. 옛 코드는 계좌를 지워도 control 플래그(모드·LIVE 확인)가 남았으나 새 코드는 `remove_account` 때 `account_modes` 행을 지운다(같은 id 로 다시 만든 계좌에 넘어가지 않게).
17. 파생 계좌 NAV 는 `account_nav` 만 읽는다(옛 `fo_nav_snapshot` 은 fo 레인 소유라 읽지 않는다) — fo 가 `account_nav` 에 쓰지 않으면 null.
18. `settings.scalp.accounts` 키는 아직 없어 lane `scalp` 는 나오지 않는다(판정 규칙만 둠).

- M8: 텔레그램 `/daily`(일일 성과 리포트)가 evaluation 에서 등록된다.
