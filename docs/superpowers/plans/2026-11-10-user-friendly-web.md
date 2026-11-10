# 사용자 중심 웹 UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans 또는 독립 영역의 dispatching-parallel-agents로 실행한다. 사용자가 전체 구현 진행을 승인했다.

**Goal:** 메뉴·정보 배치·디자인을 개선하고 시장별 현물 매수 정책을 안전하게 조작한다.
**Architecture:** 기존 기능 등록·포트와 control 원장을 활용한다. 정책 백엔드, 독립 정책 UI, 공통 디자인/메뉴를 병렬로 구현하고 오늘 화면과 전체 흐름을 통합한다.
**Tech Stack:** Python 3.12, FastAPI, SQLite, React 19, TypeScript, 기존 CSS, Vitest.
**Spec:** `docs/superpowers/specs/2026-11-10-user-friendly-web-design.md`

## Global Constraints

- 사용자 대면 메시지는 한국어. 새 의존성·셸 스크립트 없음.
- 운영 secrets 읽기·계좌 모드 변경·실제 주문·유료 AI 세션 금지.
- 웹은 원장 쓰기만. 기존 위험 청산과 계좌 보호 조건을 유지한다.
- 생성 nav는 정본 선언을 변경하고 CLI로 재생성한다.

## Review Focus

- 수동 정책 만료 뒤 자동 복귀와 US 자정 이후 운영일 일관성.
- 복수 실전 계좌 확인 누락과 잘못된 입력 차단.
- 분석 입력·최종 집행·승인 재검사 사이 정책 불일치 방지.
- 데이터 없음·조회 오류를 허용·정상으로 표시하지 않기.
- 작은 화면·긴 사유·키보드 조작과 변경 후 피드백.

### Task 1: 현물 매수 정책 백엔드

**Files:** regime policy/routes/ports 및 spot executor/tools/agents, 해당 tests.
**Interfaces:** 설계의 GET/PATCH 계약. 기존 AI view는 불변.
- [x] 정책 만료·시장 분리·실전 확인·게이트·승인·입력 회귀 테스트를 먼저 작성하고 RED 확인.
- [x] control 기반 정책과 상태 API 구현, spot 경로만 연동.
- [x] 관련 pytest와 ruff GREEN 확인.

### Task 2: 정책 UI

**Files:** `web/src/screens/system/RiskPolicy.tsx`, 타입, 전용 테스트·CSS.
**Interfaces:** 설계의 RiskMarket. `RiskPolicy`(상세)와 `RiskPolicySummary`(오늘) 컴포넌트.
- [x] 조회·실패·만료·수동 변경·실전 입력·복귀를 테스트하고 RED 확인.
- [x] 국면/AI/실효 설정과 사유·이력을 읽기 쉽게 표시하고 기존 인증/확인 흐름 재사용.
- [x] 전용 Vitest와 typecheck GREEN 확인.

### Task 3: 공통 디자인과 메뉴

**Files:** 공통 styles, nav 정본과 생성기, 화면 제목/설명/용어, 관련 테스트, `web/DESIGN.md`.
- [x] 메뉴 목적·이동·반응형 의미에 대한 회귀 테스트를 작성하고 RED 확인.
- [x] 디자인 토큰·폭·여백·타이포·44px 조작 영역과 사용자 중심 메뉴/용어를 적용.
- [x] nav 재생성, 관련 테스트와 build 확인.

### Task 4: 오늘 화면과 사용자 흐름 통합

**Files:** Today, OperationsSummary, LaneCards, DecisionsList, PauseNotice, System, ControlLog, 관련 tests.
- [x] 정책 요약 위치, 문제→해결 링크, 헤지 구분·필터, 일시정지 안내를 테스트하고 RED 확인.
- [x] 컴포넌트 연결, 세부 수집/분석 기록 접기, 화면별 목적 설명과 상태 피드백 보강.
- [x] 화면 테스트 GREEN 확인 및 전체 변경 검토.

### Task 5: 최종 검증과 PR

- [x] `uv run ruff check src tests`, 실행 중 pytest 확인 후 `uv run pytest -q -p no:cacheprovider` 최종 종료 확인.
- [x] `npm run typecheck`, `npm test -- --run`, `npm run build`, 생성 파일 검사.
- [x] 별도 개발 환경의 합성 데이터로 브라우저에서 주요 화면·모바일·테마·조작 확인.
- [x] 독립 코드 리뷰 후 지적 해결, 문서·진행표 갱신, 커밋과 PR 생성(#73).
- [ ] 주문 경로 변경 요약 제공 후 사용자 확인을 받아 머지.
