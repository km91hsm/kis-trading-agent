# 인증·작업 상태·AI 토론 안정성 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans; 독립 영역은 superpowers:dispatching-parallel-agents로 나누고 root가 통합·최종 검증한다. 사용자가 네 가지 전체 구현을 승인했다.

**Goal:** 인증과 실제 매수 범위를 알아보기 쉽게 하고, 과거 실패와 현재 작업 상태를 구분하며 AI 토론의 확인된 결함을 수정한다.
**Architecture:** 인증은 기존 tokenStore/요청 헬퍼를 재사용한다. 작업 분류는 커널의 읽기 전용 공통 계산을 API·운영 점검·React 표에서 공유한다. 토론 검증과 한 번의 기존 대체 재실행 계약은 유지한다.
**Tech Stack:** Python, SQLite, React 19, TypeScript, Vitest, pytest, 기존 CSS.
**Spec:** `docs/superpowers/specs/2026-11-10-operational-clarity-design.md`

## Global Constraints
- 한국어, 새 의존성·마이그레이션 없음. 운영 원장/설정 직접 편집·시크릿 출력·실제 주문·실제 AI 호출 없음.
- 인증/실전 확인·매수 한도·도구 단계/근거 검증·재실행 상한을 유지한다.
- 생성 nav는 CLI로만 생성. 전체 pytest는 root만 한 개씩, 에이전트의 focused pytest도 root와 조율한다.

## Review Focus
- 토큰 없음/잘못된 키/403 로컬 접속 제한을 구분하고 저장되지 않은 상태에서 성공 표시 금지.
- 조회 조건·market/ticker/account/params가 다른 성공을 실패의 후속 성공으로 연결하지 않기.
- 중단·skipped·실행 중/동시 실행을 성공 또는 해결된 오류로 오인하지 않기.
- source_id 접두사가 같거나 알 수 없는 ID·문법 오류·악성 출력에서 엄격한 검증 유지.
- 공급자 오류 메시지에 원문/비밀 노출 없이 원인 종류를 구분하고 추가 유료 호출 금지.

### Task 1: 인증·리스크 안내
**Files:** web/src/lib/api.ts, 공통 인증 안내 컴포넌트, Shell.tsx, TokenPanel.tsx, FoSwitch.tsx, ScalpSwitch.tsx, RiskPolicy.tsx, RunJobButton.tsx 및 해당 테스트/CSS. nav 정본의 표시 이름은 root가 생성한다.
**Interfaces:** tokenStore/onTokenChange, 기존 API 본문·토큰 키·#token 주소 유지. JobQueue/Runs는 이 작업에서 수정하지 않는다.
- [x] 기존 관련 테스트 baseline 확인, 인증 없음/저장 신호/403·ON/OFF 한도·조작 전 안내 RED.
- [x] 기존 인증을 유지하는 공통 안내와 화면 적용, focused Vitest GREEN. 독립 검토의 다른 탭 저장·삭제/clear 갱신 누락도 RED→GREEN 수정.

### Task 2: 자동 작업 분류와 과거 실패 안내
**Files:** kernel/daemon의 읽기 전용 activity 모듈, kernel/web/routers/jobs.py·runs.py, kernel/ops/areas.py, web/screens/system/{system.ts,JobQueue,Runs,Daemon,Health} 및 관련 테스트.
**Interfaces:** 원시 status/error는 유지. 조회 응답에 `outcome`(interrupted/failed/followed_by_success 등)·후속 정상 시각을 가산하며 구체 DTO는 구현 전 root에 공유한다. 기존 소비자는 추가 필드를 무시할 수 있어야 한다.
- [x] baseline과 중단/다른 범위/동시 실행/후속 성공·실패·skipped/24시간 경계 RED.
- [x] 공통 조회 분류·자동 작업 안내·원본 오류 및 이력 보존 구현, focused Python/Vitest GREEN.

### Task 3: AI 토론 실패 재현과 안전한 수정
**Files:** kernel/session/{research_policy,codex,runner,tooling}.py 중 확인된 원인만, tests/kernel/session/test_{codex,research_policy,research_failover,runner}.py.
**Interfaces:** 기존 DebateTurn·실제 report source_id 허용 목록·기존 한 번의 Claude 대체 재실행 유지. 주문/실제 운영 상태를 변경하지 않는다.
- [x] 과거 실패와 현재 코드 이력을 대조하고, 기존 회귀 테스트 baseline. 원문을 커밋하지 않고 합성 재현 및 실패 종류 테스트 RED.
- [x] 확인된 원인만 최소 수정, 없는 근거 거부·실패 유형 비밀 비노출·취소 시 재시도 금지·한 번의 복구 검사 GREEN.

### Task 4: 통합·검토·배포
- [ ] 문서와 생성 메뉴 갱신. 전체 typecheck/Vitest/build/ruff/Python 및 생성 파일 검사.
- [ ] 합성 미리보기에서 인증 없음·오류/중단/후속 정상·리스크 안내, 모바일/두 테마를 확인.
- [ ] 독립 스펙/규칙 검토와 지적 수정, 검증 기록, 커밋·PR·최신 head CI·머지.
- [ ] main 동기화·앱 빌드/스모크·백업·정상 교체/재시작, 설정/제어값 보존 및 운영 화면 검증.
