# 상세 화면과 이동 흐름 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans, 독립 파일 영역은 superpowers:dispatching-parallel-agents로 병렬 구현한다. 사용자가 검토 범위 전체 구현을 승인했다.

**Goal:** 계좌·시장 맥락을 유지하고 파생·고급 분석·작업 상세를 필요한 화면에서 읽기 쉽게 제공한다.
**Architecture:** 기존 React Router의 URL 상태와 useResource를 사용한다. 공통 hash 이동/오류 UI는 root가, 계좌·성과/데이터·자동 작업은 독립 영역으로 구현한다. 주문 규칙과 운영 상태는 바꾸지 않는다.
**Tech Stack:** React 19, TypeScript, 기존 CSS, Vitest, Python 기능 nav 선언.
**Spec:** `docs/superpowers/specs/2026-11-10-context-friendly-web-design.md`

## Global Constraints
- 한국어 UI, 새 의존성 없음, 운영 secrets·state 편집과 실제 서비스 실행 없음.
- 생성 nav는 정본 FEATURE 선언 변경 후 생성한다.
- 전체 pytest는 동시에 하나만, 프런트 최종 gate도 root가 통합 후 실행한다.

## Review Focus
- 잘못된 URL·삭제된 계좌·지원하지 않는 시장에서 다른 계좌/시장의 값이 조용히 남지 않기.
- 브라우저 뒤로가기와 새로고침, 비동기 로딩 뒤 anchor, 사용자 스크롤 후 강제 이동 금지.
- 접힌 상세의 기존 링크와 파생 주문 상세 접근 유지.
- 모의/실전·통화·시장 분리와 실제 주문/AI 영향의 불확실성 명시.
- 375px·키보드·두 테마에서 주요 조회와 재시도 가능.

### Task 1: 계좌 맥락과 파생 상세
**Files:** accounts/Account.tsx, Accounts.tsx, PositionsTable.tsx, 계좌 전용 테스트·CSS, 파생 주문 다리 컴포넌트.
**Interfaces:** 계좌 URL `?market=KR|US`, 파생 주문 다리 `account_id` 단위. 종목 링크는 `/data?market=...&ticker=...&exchange=...#stock-chart`.
- [x] 복수시장 위험/성과 조회·잘못된 시장·종목 이동·보유 우선·접힌 세금·계좌별 파생 다리 테스트 RED.
- [x] 최소 구현과 관련 테스트 GREEN. 다른 작업의 System/공통 파일은 변경하지 않는다.

### Task 2: 성과 맥락과 종목 데이터
**Files:** performance/Performance.tsx, Overview.tsx, performance.ts, data/Data.tsx, 관련 테스트·전용 CSS.
**Interfaces:** 성과 URL `account_id`, `market`, `days`를 PageTabs에 보존. 기존 종목 차트 URL 보존.
- [x] 새로고침 가능한 필터·잘못된 값 보정·고급 분석 접기·파생 성과 위치·검색 우선 순서 테스트 RED.
- [x] 기존 화면·API와 호환하는 구현 및 관련 테스트 GREEN.

### Task 3: 작업 상태와 실행 영향 안내
**Files:** system/JobQueue.tsx, JobQueue.css, components/RunJobButton.tsx, 관련 테스트.
**Interfaces:** 기존 POST /jobs와 확인 필드는 그대로. 표시용 영향 정보는 확인 가능한 메타데이터/호출 인자로만 설명하고 미확인은 명시.
- [x] 요청 기록 우선·수동 실행 접기·대상/영향 확인·요청 이후 피드백 테스트 RED.
- [x] 확인 강도를 유지하는 구현 및 관련 테스트 GREEN.

### Task 4: 공통 이동과 오류 복구, 정책 화면 통합
**Files:** app/shell/Shell.tsx, lib의 hash 이동 모듈, components/Empty.tsx, performance/ResourceView.tsx, system/System.tsx, 관련 테스트.
**Interfaces:** 동적 anchor를 관찰하고 사용자 입력 시 중단. `body(res,what,render)` 호출 계약 보존, res.reload를 재시도에 사용.
- [x] 지연 anchor/접힌 절/사용자 스크롤·재시도와 빈 상태·파생 정책 상세 제거·용어 정합성 테스트 RED.
- [x] 구현 GREEN, 세 독립 영역과 통합.

### Task 5: 검토·배포 가능한 PR
- [x] 전체 웹 typecheck·Vitest·build와 생성 메뉴 검사. Python 표시 변경 focused 검사 통과. 전체 검사 중 수집 확장 재시작 테스트 실패는 검증 기록에 별도 명시한다.
- [x] 합성 환경에서 계좌/시장 선택·새로고침·anchor·오류 재시도·모바일·두 테마를 확인.
- [x] 독립 전체 diff 검토, 지적 수정, 문서·진행표·검증 기록 갱신.
- [ ] 커밋·PR·CI 확인 후 일반 UI PR 머지, main 동기화. 운영 앱 적용은 사용자 전용 실행 경계 유지.
