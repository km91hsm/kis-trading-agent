# scalp

## 책임

- 분 단위 단타 레인(tier=lane): 1분봉 신호는 파이썬 순수 함수가 감지·집행하고, 에이전트는 후보와 허용 여부(strategist `scalp_ok`)·진입 판정(`scalp-judge`)·보유 조언만 낸다.
- **모의 전용(paper only)·기본 끔**: `settings.yaml scalp.enabled: false`. 실전 계좌는 설정이 켜져 있어도 기록만 한다.
- 설정: `settings.yaml scalp`(`ScalpCfg`, 17필드). 판정자는 `judge: false` 가 기본.
- 레인이라 다른 기능을 import 하지 않는다. 필요한 것은 포트(deps 주입)로 받는다.

## 포트

| 포트 | 용도 |
|---|---|
| `accounts` | 계좌 목록·모드 조회 |
| `regime` | 국면·킬스위치 조회 |
| `scan` | 후보(picks·뉴스·watch) 조회 |
| `stock_exec` | 주문 집행(executor 코어 경유) |

## 데몬 잡

| 잡 | 시각 | 비고 |
|---|---|---|
| (Task 7 에서 채움) | | |

## 알려진 차이 (옛 저장소 대비)

- 초안 — Task 8 에서 마무리
