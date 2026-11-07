# Market Evidence Followup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 실제 시세·입력·모델 토론을 검증하고 검증된 버전을 운영에 반영한다.

**Architecture:** 기존 broker/입력 포트와 두 CLI를 재사용한다. 제품 변경은 공통 입력 축약과 실제 검증에서 재현된 결함에 한정한다. 조회·모델 비교는 격리 작업 자료로 남기며 운영 단계 전에는 production state를 바꾸지 않는다.

**Tech Stack:** Python3.12, SQLite, httpx, pytest, Claude/Codex CLI, React 기존 빌드.

**Spec:** `docs/superpowers/specs/2026-11-07-market-evidence-followup.md`

## Global Constraints

- 8000 UTF-8 bytes 공통 context; 240→140→64자 블로그 발췌, 제목 최대160자. URL·출처·시각 불변.
- 실제 모델 최초6회, 실패 턴만 최대1회 재시도. 실제 주문·계좌 모드 변경·신규 유료계약 금지.
- 전체 pytest는 한 번에 하나. 실제 원문·계좌·키를 공개 저장소에 커밋하지 않는다.
- 운영 변경은 Task5에서만. Codex 설정 기본OFF와 기존 계좌 모드를 유지한다.

## Review Focus

- 긴 한국어 블로그5건이 첫 글1건으로만 축약되는 회귀.
- 식별자가 매우 길어 5건을 담을 수 없을 때 coverage가 성공으로 남는 문제.
- 블로그를 보존하느라 매크로 값/활동 시각 또는 결측 표시가 사라지는 문제.
- 실제 CLI가 JSON schema/환경/권한 제한과 다르게 동작하는 문제.
- 운영 재시작 시 자동작업·원장버전·기존설정이 준비 당시와 달라지는 문제.

### Task 1: KIS read-only validation

**Files:** private plan workspace `probe_kis.py`, `kis-live-validation.json`; 필요시 `src/trader/kernel/broker/market_activity.py`, 해당 `tests/kernel/broker/test_activity_rank.py`.
**Interfaces:** consumes `KisMarket.activity_rank(market,kind,exchange,max_pages,limit)` and `index_daily`; produces sanitized observations/explicit gaps.

- [ ] 기존 안전한 인증 조립·공유캐시 경로 확인 후 GET 시세만 허용하는 probe를 작성한다.
- [ ] KR/US 거래대금/등락과 SPX/COMP를 페이지1·소량으로 읽고 rt_cd/자료형태/관측정보를 기록한다. Expected: 성공자료 또는 원인별 실패, 비밀값 출력없음.
- [ ] 공식 원천과 실제 숫자로 단위·시점의 확인 범위를 기록한다. Expected: 미확정은 unknown, 휴장 동일값으로 증가율 실측 주장없음.
- [ ] 결함 재현 시 합성 RED→최소수정→GREEN을 먼저 완료한다.

### Task 2: Preserve recent blog evidence in compact context

**Files:** `src/trader/kernel/session/tooling.py`, `tests/kernel/session/test_tooling.py`, `docs/market-data-codex.md`.
**Interfaces:** consumes `compact_context(inputs:dict)->dict`; preserves same signature and 8000-byte contract.

- [ ] 5건 긴 한국어 글+매크로+활동 fixture를 추가해 발췌5건·원래 URL/출처/발행/수신시각·핵심매크로값·전체8000bytes를 검사하고 RED를 확인한다.
- [ ] `compact_context`에서 본문을 먼저 축약하고 최신5건을 담는 공간을 확보한다. 불가능할 때는 실제 생략량·상태를 유지한다.
- [ ] 과도한 URL/많은글/빈본문/입력불변/재축약에 대한 회귀를 추가한다.
- [ ] `uv run pytest -q tests/kernel/session/test_tooling.py tests/kernel/session/test_debate_compaction.py tests/features/ingest/test_input_quality.py tests/features/ingest/test_ports.py` Expected: PASS. Ruff와 문서 갱신 후 커밋.

### Task 3: Actual CLI debate validation

**Files:** private workspace `compare_debate.py`, `debate-evaluation.md`; 결함 발생 시 `src/trader/kernel/session/codex.py`/관련 tests.
**Interfaces:** consumes `codex.run_turn`, `DebateTurn`, same frozen public/synthetic reports for Claude and Codex.

- [ ] 실제 설치 CLI의 도움말·인증 준비를 출력값 노출 없이 확인하고, 도구/운영경로 미허용 임시환경을 준비한다.
- [ ] 각 모델 독립1·반론1·재답변1을 실행한다. Expected: 유효JSON, 상대 모든쟁점응답, 근거ID유효, 정확시점/대체자료인식.
- [ ] 실제 출력의 근거·상호반론·판단변경·시간·토큰을 비교한다. 실패는 성공으로 대체하지 않는다. 필요 수정은 합성 RED/GREEN 후 실패턴만 재검증한다.

### Task 4: Exact macro provider decision

**Files:** `docs/research/2026-11-07-exact-macro-sources.md`, 운영안내.
**Interfaces:** prior official WTI daily-ccnl/CL master and ICE DXY investigation.

- [ ] 권한/비용/스키마 확인 여부를 기록하고 실제 권한이 없는 원천은 비활성으로 남긴다. Expected: proxy 유지 또는 검증된 정확원천만 활성.
- [ ] 후속 공식조사에서 확인한 일별API·월물변경·권한 조건을 문서화한다.

### Task 5: Review, merge and controlled deployment

**Files:** final private evidence/backup manifest; production tracked source, built web, sources config through reviewed update only.

- [ ] ruff·전체pytest1개·생성파일검사·웹검증과 독립 whole-branch review를 수행한다. Expected: PASS/no unresolvedImportant.
- [ ] PR·CI를 완료하고 검증된 head를 머지한다. 주문경로 자체 변경이 새로 생기면 구체diff로 기존확인경계를 적용한다.
- [ ] 실행작업/대기주문/상태를 확인하고 서비스정상종료·최신백업 후 원격main과웹을반영한다. ranto28만기존sources에병합, 계좌모드/Codex토글보존.
- [ ] 공식부트의원장마이그레이션·서비스재시작 후 PID/heartbeat/웹/원장버전/collector 상태를 읽기만확인한다. Expected: 운영17, 기존기록보존, 설정반영, 실주문/실모델수동시험없음.
- [ ] 장중이력부족·정확DXY/WTI조건·수익성미검증을최종보고서에구분한다.
