# fo

## 책임

- 선물옵션 방향성 레인(tier=lane): 코스피200 선물 롱/숏·옵션 콜/풋 매수·매도. **모의 파생 계좌 전용·기본 끔**(`settings.yaml fo.enabled: false`). `enabled` 는 신규 진입만 막고 보유분 감시·축소는 계속한다.
- 설정: `settings.yaml fo`(`FoCfg` + 하위 모델 6종, 상품 모델은 커널 `broker.models.FoProduct`), `schedule.yaml fo`(`FoSchedule`: `kr.session` 08:20 · `kr.monitor` 08:45~15:45 60s).
- 원장: 마이그레이션 14 `fo` — `fo_positions`·`fo_order_legs`(`request_id` 포함)·`fo_decisions`·`fo_nav_snapshot`·`fo_positions_snapshot`.
- 순수 모듈: `schemas.py`(결정 스키마)·`types.py`(상태 전이표·행 타입·`FoExecContext`)·`pricing.py`(Black-76 재평가)·`messages.py`(알림 15종).
- 레인이라 다른 기능을 import 하지 않는다. 필요한 것은 포트(`FoExecContext.ports`, deps 주입)로 받는다.

## 포트

| 포트 | 용도 |
|---|---|
| `accounts` | 파생 계좌·브로커(`fo_accounts()`/`fo_brokers()`)·시세 클라이언트·모드·일시정지 |
| `regime` | 국면·킬스위치·시장 뷰 조회 |

## 데몬 잡

| 잡 | kind | trigger | schedule_key | gate | 비고 |
|---|---|---|---|---|---|
| (뒤 Task 가 채움) | | | | | |

## 알려진 차이 (옛 저장소 대비)

(Task 9 에서 채운다.)
