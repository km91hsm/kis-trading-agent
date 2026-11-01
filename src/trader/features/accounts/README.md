# accounts

## 책임

- `config/accounts.yaml` 의 계좌 목록(모델·검증·cano 자리표시자 채움·markets 기본 `["KR","US"]`)과 계좌 모드(`off|approve|auto`)를 소유한다.
- 실전 계좌를 `auto` 로 두려면 LIVE 확인이 선행돼야 한다(`LIVE_CONFIRM = "LIVE"`). 모드·확인 상태는 원장 테이블 `accounts`·`account_modes`(마이그레이션 v2)에 둔다. 전역 일시정지는 커널 control 플래그 `paused`(`control_namespaces=("paused",)`).
- 계좌별 KIS 키(`KIS_PAPER_*`/`KIS_REAL_*` 접두 시크릿)로 시세·브로커를 조립한다(`brokers.py`). `secrets/.env` 가져오기·카탈로그·갱신은 `secrets_io.py`, 계좌 추가·삭제는 `accounts_io.py`.
- 텔레그램 명령 `/pause /resume /mode` 와 `/status` 의 "계좌" 절을 `kernel/notify` 디스패처에 등록한다.

## 공개면 (`api.py`)

`LIVE_CONFIRM`, `Account`, `ConfirmationRequired`, `Mode`, `account_mode`, `build_brokers`, `build_market`, `effective_mode`, `get_account_mode`, `is_paused`, `load_accounts`, `market_creds`, `split_accounts`, `to_broker_account`. 다른 기능은 이 파일만 import 한다. `routes=()`(웹 라우트는 M11).

CLI: `trader accounts show | mode | pause | resume | ping | add | remove | secrets import`.

## 규약

- 같은 값으로 모드를 다시 설정하면 쓰기를 하지 않는다(`set_account_mode`). `effective_mode` 는 실전 계좌가 `auto` 인데 LIVE 미확인이면 `approve` 로 낮추고 `LIVE_UNCONFIRMED_NOTE` 를 붙인다. 실전 파생 계좌는 주문이 없다(`REAL_FO_OFF_NOTE`).
- 같은 키를 여러 계좌가 쓰면 경고(주식 WARNING, 파생 INFO). 키가 없는 계좌, `fo_product` 가 없는 파생 계좌는 경고 후 건너뛴다.
- 시크릿 값은 로그·stdout·예외 메시지에 넣지 않는다(이름과 '설정됨' 여부만). 로거는 `trader.config`(옛 호환)·`trader.accounts`.
- 코드는 `kernel/` 만 import 한다(3층 규칙).

## 테스트

`tests/features/accounts/` — 모드·`accounts_io`·`secrets_io`·다중 계좌 키·CLI·텔레그램 명령.

## 알려진 차이 (옛 저장소 대비)

1. `control.mode:*`·`live_confirmed:*` 플래그 → `account_modes` 테이블(스펙 §2.4).
2. CLI: `control` → `accounts`, `kis ping` → `accounts ping`, `secrets import` → `accounts secrets import`. `accounts remove` 는 성공 시 `삭제: …` 를 출력한다(옛 출력에 없던 줄).
3. `/watch`·watchlist 는 M7(scan).
4. `/approve`·`/reject`·`/run`·`/kill`·`/daily`·`/fo` 는 각 M(M4·M7·M6·M8·M10). `/status` 의 데몬·국면·스캔·미승인 줄도 해당 M 이 `status_sections` 로 더한다.
5. `exec-key list|release` 는 M4.
6. `accounts_io.blockers` 는 테이블이 없으면 그 사유를 생략한다. `DEAD_STATUSES` 는 이 기능 안의 로컬 상수이며 M4 가 커널로 옮긴다. `test_accounts_io.py` 의 `db` fixture 는 M4 전까지 최소 스탠드인 테이블을 CREATE 한다.
7. 체결통보 리스너(`_notice_listeners`)는 M5.
8. `ensure_web_token`(`secrets_io`)은 아직 어디에도 연결되지 않았다. 연결·키 웹 라우트는 M11.
9. `parse_command("/foo")` 는 이름 `foo` 를 돌려준다(거부는 디스패처의 몫).
10. `REAL_FO_OFF_NOTE` 문구를 옛 "Phase 26 범위 밖" 에서 "실전 파생 계좌는 주문 없음(파생 레인 범위 밖)" 으로 바꿨다.
11. 옛 `SCALP_EXIT` 표·`DecisionBrief` 는 `notify.messages` 에 이식하지 않았다(M4/M5/M9 가 더한다).
12. `/status` 는 한 절이 예외를 내면 명령 전체가 실패한다(절별 격리 없음).
13. `notify.enabled=false` 와 토큰 없음이 `telegram poll|loop` 에서 같은 토큰 오류 문구로 나온다(원인 구분 없음).
14. 명령별 echo 는 `name arg` 한 줄로 통일했다. `/mode <id> fast` 는 옛 코드처럼 "거부: …".
15. `_COMMAND_RE` 는 `[a-z_]+` 라 숫자가 든 명령 이름은 쓸 수 없다. 핸들러는 `load_accounts()` 를 시크릿 없이 호출한다.
