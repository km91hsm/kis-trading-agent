# WEB UI Operations Implementation Plan

> **For agentic workers:** Use independent scoped implementers under dispatching-parallel-agents; each task uses test-driven-development. Parent integrates and conducts whole-branch code-review. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 수집·입력·매크로·토론·거래대금의 실제 상태를 UI에 드러내고 운영 오판을 줄인다.
**Architecture:** 기존 기능별 읽기 API와 저장된 스냅샷/체크포인트를 이용한다. 기능별 컴포넌트를 분리하고 공통 스타일/오늘 요약은 통합 단계에서 적용한다.
**Tech Stack:** Python 3.12/FastAPI/SQLite, React 19/TypeScript/Vite/Vitest.
**Spec:** docs/superpowers/specs/2026-11-07-web-ui-operations.md

## Global Constraints
- 스펙 제약 전부 적용. 실제 주문/모델 호출/유료 계약/계좌 모드 변경 없음.
- 기존 토큰·폰트·반응형 구성 유지, 추가 의존성 없음.
- 저장 입력과 전달 입력, 전달과 실제 근거 인용, missing과error를 구분한다.
- 작업별 파일 소유권을 지키고 공통 파일·커밋은 부모가 통합한다.

## Review Focus
- 휴장/초기 설치의 빈 자료를 실패 또는 정상 수집으로 오인하지 않는가.
- 미래 시각/손상 자료/오래된 관측/0건 응답을 최신 유효값으로 표현하지 않는가.
- 과거 세션을 현재 수집값이나 현재 모델 설정으로 덮어 쓰지 않는가.
- 근거 링크의 javascript:/잘못된 URL 및 외부 원문·비밀 노출을 막는가.
- 390px/다크모드/긴 한글과 영문 쟁점명/키보드 조작에서 정보를 잃지 않는가.

### Task 1: SaveTicker 상태와 스트림 현황
**Files:** features/saveticker routes/status helper + tests; web screens/system/SavetickerStatus.tsx(new), System.tsx, system.ts 및 전용 tests/CSS. feature.py nav label은 부모 통합.
**Interfaces:** GET /system/saveticker 기존키 유지 + streams[]/summary/recovery의 읽기 정보. UI에서 정상/대기/로그인필요/오류 한글 매핑.
- [ ] healthy/collecting, lane partial, 0건, unchanged, 손상/누락/미래시간 합성 테스트 RED.
- [ ] bounded read-only 스트림 상태 DTO와 표시 구현; 내부 원문/전체 파일경로를 반환하지 않는다.
- [ ] 대상 pytest/Vitest GREEN, 자체 검토 후 결과/변경 파일 전달.

### Task 2: LLM 입력 내역과 쟁점별 토론
**Files:** features/spot routes + new input evidence helper; optional read-only context receipt hooks only if required; screens/decision new Evidence/Comparison components, Decision.tsx, lib/trace.ts 관련 tests/CSS.
**Interfaces:** GET /decisions/spot/{id}/inputs 로 세션 시점 입력/출처/전달 증거를 반환. 기존 trace는 호환 유지. UI 외부 링크는 http(s)만.
- [ ] 과거 미기록, 정상/부분 입력, 5블로그, provider미기록, 쟁점ID연결/입력불변/위험링크 RED.
- [ ] 정확히 기록된 전달과 재구성/미기록 구분; 수집값 및 상대 쟁점을 보기 좋은 한글 표/열로 연결.
- [ ] 대상 pytest/Vitest GREEN; 실제 모델 호출 없이 합성으로 검증.

### Task 3: 저장된 매크로 5종 요약
**Files:** features/ingest/macro_view.py(new), routes.py (+router export), tests; web screens/data/MacroOverview.tsx(new), 전용 tests/CSS. Data.tsx는 부모 통합.
**Interfaces:** GET /macro?market=KR|US : items[sp500,nasdaq,us10y,wti,dxy], 값/단위/날짜/출처/대체/등락/상태/저장시각. <MacroOverview market={market}/>.
- [ ] 빈/손상/미래/중복일/미제공등락/대체지표/마지막저장관측 합성 RED.
- [ ] 이미 저장된 ingest snapshot만 bounded read; 외부 네트워크/새 수집 없음.
- [ ] 대상 pytest/Vitest GREEN, UI 데이터 페이지 삽입은 부모에게 전달.

### Task 4: 거래대금 탐색과 상태 의미
**Files:** features/quotes/market_activity.py/routes.py; tests/features/quotes; screens/data/MarketActivity.tsx + helper/tests.
**Interfaces:** 기존 API 키 유지. coverage.pending/failed/market_session/collection_state 추가; growth 이유 유지. ranking/sort/filter URL 동기화.
- [ ] missing은pending, 실제error는failed, 장밖대기/장중지연 구분 RED.
- [ ] 원천 관측 보존 상태 DTO, 증가율 정렬/필터/차트 링크·이유 안내 구현.
- [ ] targeted Python/Vitest GREEN, 미확정통화·세션을 확정하지 않는지 검증.

### Task 5: 운영 요약·화면 정리·접근성 통합
**Files:** screens/today/OperationsSummary.tsx(new), Today.tsx; kernel/web operations readonly route if required; Data.tsx; SettingsPanel.tsx; Shell.tsx/styles; feature nav declarations/gen; web/DESIGN.md.
**Interfaces:** 기존 system/usage/daemon/codex-debate/saveticker/clock APIs 재사용 우선, 시장시간은 서버 판정 재사용. 전달 받은 MacroOverview 삽입.
- [ ] 제한·자료없음·모델OFF·과거날짜현재상태 라벨 및 keyboard/URL 동작 RED.
- [ ] 운영 요약, 설정 바로가기/리스크 값, 중복 제목 정리, 본문 바로가기 및 스타일 구현.
- [ ] 대상 테스트 GREEN 및 390/768/1440px 라이트·다크 실제 브라우저 확인.

### Task 6: 통합 검증·리뷰·머지
**Files:** docs 설명/검증 기록 및 private evidence.
- [ ] ruff/gen-nav/gen-sse/typecheck/Vitest/build/전체 pytest 한 번 실행. 모두 최종 종료 확인.
- [ ] fresh spec/standards 리뷰, 실제 결함은 RED→GREEN 수정, GitHub CI/리뷰 스레드 확인 후 PR 머지.
- [x] 운영 반영과 재시작은 기존 사용자 지시에 따라 보류한다. 운영 checkout과 설정·원장은 변경하지 않는다.
- [ ] 완료 항목과 장중/장기잠금/정확DXYWTI 등 별도 검증 잔여를 구분해 보고.
