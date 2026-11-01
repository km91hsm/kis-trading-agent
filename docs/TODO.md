# TODO — 마일스톤 체크리스트

정본은 스펙 §3. 각 M 은 브랜치 1개·PR 1개이고, 옛 테스트를 새 구조로 옮겨 통과해야 완료다.

## M0 — 뼈대
저장소·uv·ruff·CI·templates·`kernel/{config,platform,registry,ledger 코어,daemon 셸,web 셸,cli 루트}`·계약 테스트 선행. 완료 기준: CI 녹색, `trader init`·`selfcheck` 동작.
- [x] Task 1 저장소·pyproject·uv·ruff·`.gitignore`·CI 골격
- [x] Task 2 `kernel/platform`
- [x] Task 3 `kernel/config`·`templates/config/`
- [x] Task 4 `kernel/ledger` 코어
- [x] Task 5 `kernel/registry`(계약·import 경계 검사)
- [x] Task 6 `kernel/daemon` 셸
- [x] Task 7 `kernel/web` 셸·SSE 키 생성
- [x] Task 8 `web/` Vite 스캐폴드
- [x] Task 9 `kernel/cli`(`init`·`selfcheck`·`daemon`·`web`·`dev`)
- [x] Task 10 문서(`CLAUDE.md`·`docs/TODO.md`·`docs/architecture.md`·`README.md`)
- [ ] Task 11 저장소 공개·PR·CI·머지

## M1 — kernel/broker
KIS auth/client/market/broker/ws/fake·`tr_ids`·교차 프로세스 레이트리미터. 완료 기준: `kis ping` 코드 경로 테스트 통과.
- [ ] 착수 전

## M2 — features/accounts + notify
계좌 yaml·모드·LIVE 확인·키·추가삭제, `kernel/notify` 전송·outbox. 완료 기준: 옛 `control/*`·`notify` 테스트 이식 통과.
- [ ] 착수 전

## M3 — features/ingest + saveticker
수집(KIS·DART·EDGAR·뉴스·커뮤니티·거시)과 saveticker 수집기·리더·데몬 잡. 완료 기준: 옛 `sources/*`·`saveticker/*` 테스트 이식 통과.
- [ ] 착수 전

## M4 — kernel/session + kernel/execution + spot analyze
SessionKind 레지스트리·FAKE·executor 코어·`features/spot` analyze 경로(가장 큼, 골든 회귀 1회 재기준). 완료 기준: `FAKE_CLAUDE=1 trader analyze --dry-run` 체크포인트 일치.
- [ ] 착수 전

## M5 — spot 운영 경로
monitor·resume·손절·fills·notice·reconcile·balance·reprice. 완료 기준: 옛 테스트 이식 통과.
- [ ] 착수 전

## M6 — features/regime + 의도 생산자
국면·stance·시장 세션·킬스위치·블랙아웃, hedge·cash_park·rebalance, scan 의 테마·사건. 완료 기준: 옛 테스트 이식 통과.
- [ ] 착수 전

## M7 — features/scan + ml + jobs
quant·screener·discovery·유니버스 PIT, 피처·학습·레지스트리·랭커·rl, 작업 큐. 완료 기준: 옛 테스트 이식 통과.
- [ ] 착수 전

## M8 — features/evaluation + briefing
메모리·보정·reflect·섀도·성과·readiness·backtest·paper_review·일일 리포트, 아침 브리핑. 완료 기준: 옛 테스트 이식 통과.
- [ ] 착수 전

## M9 — features/scalp
단타 Lane. 완료 기준: 옛 `scalp/*`·`test_daemon_scalp` 이식 통과.
- [ ] 착수 전

## M10 — features/fo
파생 Lane(`LegStore`, 커널 executor 코어 위). 완료 기준: 옛 `fo/*` 계열 테스트 이식 통과.
- [ ] 착수 전

## M11 — features/quotes + 웹 API 12 자원 (백엔드만)
웹 API 12 자원·SSE 키 생성·계약 테스트. 완료 기준: 모든 자원 엔드포인트 테스트 통과, 옛 엔드포인트 대응표 100%. (M11b: 새 웹 UI 구현은 별도 브레인스토밍·플랜)
- [ ] 착수 전

## M12 — kernel/ops + 번들 + 전환
health·watchdog·status·retention, CLI 전체 점검, 번들(`packaging/build.py`·`build.yml`), `CLAUDE.md` 최종 다이어트, 전환 리허설. 완료 기준: `FAKE_CLAUDE=1 trader daemon --dry-run` 하루 무사고 → 사용자가 `install` 교체.
- [ ] 착수 전

## 사용자 확인 대기 (옛 저장소 승계)

옛 저장소에서 아직 미확인인 실기 항목. 새 저장소 해당 기능이 이식된 뒤 확인한다.

- [ ] 실기(사용자 확인 대기, 영업일 09:00~15:30 KST): KIS 주문 테스트 — `kis order-test --account paper-main` → 1주 지정가(현재가 −10%) 접수·취소 메시지 확인 (M1·M2 이후)
- [ ] 실기(사용자 확인 대기): 텔레그램 승인 — 모드를 `approve` 로 두고 BUY 결정 때 승인 요청 → 텔레그램 `/approve <id>` → 주문 접수 확인 → 모드 `auto` 원복 (M2·M4 이후)
- [ ] 사용자 확인 대기: `secrets/.env` 에 `WEB_TOKEN` 설정 후 `trader web` → 설정 페이지에 토큰 입력 → 일시정지/재개가 반영되는지, 승인 요청이 있을 때 승인 → 주문 접수 확인 (M11 이후)
- [ ] 실기(사용자 확인 대기): 번들 zip 을 풀어 더블클릭 → 웹 UI → `kis ping` (macOS 우클릭 열기·Windows SmartScreen 1회) (M12 이후)
- [ ] 실기(사용자 확인 대기): `Trader.app` 더블클릭 → 메뉴바 아이콘·브라우저 → 메뉴에서 일시정지/재개·로그 폴더·종료(실행 중 세션 대기 후 잠금 해제). Windows 는 `TraderApp.exe` 작업표시줄 아이콘·콘솔 창 없음 확인 (M12 이후)
- [ ] 실기(사용자 확인 대기, 모의): KR 잔고 응답 `output2.tot_evlu_amt`·`nass_amt`·종목 `ord_psbl_qty` 필드 존재, US 잔고·체결이 NASD·NYSE·AMEX 3회 조회로 합쳐지는지, 재부팅 뒤 `daemon.lock` 이 stale 로 넘겨지는지 (M5 이후)

## 이연/추후

- SDD 리뷰에서 이연한 Minor 항목은 `.superpowers/sdd/2026-11-02-m0-skeleton/progress.md` 의 각 Task "minor (deferred)" 줄에 있다. M0 최종 리뷰에서 일괄 판단한다.
