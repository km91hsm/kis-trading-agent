# 전체 일시정지 중에도 위험 청산은 계속 (설계)

날짜: 2026-11-09 · 크기: 프로젝트(세 레인 주문 경로) · 기반: `feat/scalp-toggle-and-pause-labels`(PR #49) 위에 쌓는 별도 PR

## 의도
사용자는 전체 일시정지를 「새 주문만 멈추는 스위치」로 이해한다(화면 문구도 「새 주문만 멈춤」). 그런데 지금 코드는 일시정지 중 손절·단타 청산·파생 손절/만기 청산까지 막아, 일시정지를 켜 둔 사이 손실이 커질 수 있다. 일시정지가 **위험 청산은 막지 않게** 바꾼다.

## 사용자 결정
- 일시정지 중에도 나가는 것: **위험 청산만** — 현물 손절, 단타 청산 전부(손절·익절·시간 청산·다음 날 이월 청산), 파생 손절·만기/강제·보유기간·장 마감 청산·축소(RESIZE_DOWN·continue_reduce)·대기 진입 회수.
- 일시정지 중에도 멈추는 것: 매수·새 진입·비중 확대, 그리고 **판단 매도**(분석 SELL/TRIM, 재평가 세션, 리밸런스 축소, 헤지·현금 파킹 해제, 승인 대기 주문 실행(매수·매도 모두), 아침 지정가 재호가, 파생 재평가 CLOSE/REPLACE).
- 별도 PR.

## 현재 막는 곳(조사 결과)과 바꿀 곳
| 레인 | 지금 | 바꿈 |
|---|---|---|
| 커널 실행 코어 | `ExecPolicy.transmits = mode!="off" and not paused and not dry_run` — 매수·매도 구분 없음 | 위험 청산 표시(예: `risk_exit=True`)가 붙은 실행은 `paused` 를 보지 않는다(모드 off·dry_run 은 그대로). 커널은 기능을 모르므로 일반 필드로. `core.approve`(승인 실행)는 그대로 막음 |
| 현물 손절 | `monitor._sell_now`: paused 면 미체결 취소도 안 하고 `stop_paused` 알림만 | 손절 매도·그에 필요한 미체결 취소를 진행, 실행기에 위험 청산 표시(`origin=="stop"`/`is_stop_loss`). `stop_paused` 알림은 「일시정지 중 손절 실행」 안내로 |
| 현물 재평가·리밸런스·헤지·파킹·재호가 | paused 면 막음 | 그대로(판단 매도) |
| 단타 | 잡이 paused 면 틱 전체 skip, `exits.can_trade` 도 paused 면 대기 | 잡은 paused 여도 틱을 돌리고(창·휴장 게이트는 그대로), `can_trade` 에서 paused 조건 제거. 진입은 `engine.allowed()` 의 「일시정지」로 계속 막음. 판정 대기(pending)는 일시정지 중 진입으로 정산하지 않음 |
| 파생 | `monitor_job` 이 paused 면 틱 skip, 실행기 `record_only`·`rules.evaluate` 가 paused 면 축소 다리도 기록만, engine `NO_STATE_CHANGE` 에 paused | `monitor_job` 은 paused 여도 틱(세션·재평가 요청 잡은 그대로 skip). 축소 다리(`reduce`·`continue_reduce`·`recall_entries`)와 rules 의 reduce 판정은 paused 를 보지 않음, 진입 다리(`run_leg` 의 진입)는 기록만 유지. 실제 청산이 나가면 정산 전이·halted 플래그도 정상 처리(위험 청산 경로에서 paused 를 NO_STATE_CHANGE 로 보지 않음) |
| 게이트 | market 게이트는 paused 면 rebalance·scan_analyze 등 skip, monitor 게이트는 paused 무시 | 그대로 |

## 화면·문구
- 헬스 「자동매매: 전체 일시정지 중 — 새 매수·진입과 승인을 멈춤 · 손절·청산은 계속」, 대시보드 띠 「… 새 주문만 멈춤(손절·청산은 계속)」, ▲ 메뉴 확인창 문구, 계좌 화면 보조 표기 「(전체 일시정지로 새 주문 멈춤)」.
- CLAUDE.md 불변식 「paused: 신규 주문 경로는 모두 멈춘다」 → 「신규 진입·판단 매도·승인 실행을 멈춘다. 위험 청산(손절·단타 청산·파생 손절/만기/축소)은 계속」. user-guide·architecture(Ruling 20)·각 레인 README·docstring 동기화.

## 비목표
- 판단 매도·승인 실행을 일시정지 중 허용하는 것(사용자 결정으로 제외).
- 계좌 모드 off·dry_run 의 의미 변경(off 계좌는 여전히 아무 주문도 안 나감).
- 일시정지 자체의 켜고 끄는 방식.

## 빌더 몫
위험 청산 표시 필드 이름·전달 방식, 알림 문구 세부, 파생 상태 전이 세부(테스트로 고정), 기존 테스트 중 「일시정지면 청산도 막힘」을 단언하던 것들의 갱신.
