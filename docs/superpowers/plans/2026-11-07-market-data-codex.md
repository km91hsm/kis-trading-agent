# KIS Market Data and Codex Debate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 브라우저와 독립된 KIS 시장 자료를 LLM에 전달하고 Codex를 켜고 끌 수 있는 상호 토론을 제공한다.

**Architecture:** quotes의 지속 수집/저장/읽기와 kernel/session의 제한된 Codex CLI 브릿지를 추가한다. 서비스 공개면과 포트로 ingest·시장·scout에 연결하며 기존 Claude 오케스트레이터·리스크 엔진은 유지한다.

**Tech Stack:** Python3.12, Pydantic2, SQLite, KIS REST, Codex CLI, React/TypeScript, pytest/respx/vitest.

**Spec:** `docs/superpowers/specs/2026-11-07-market-data-codex-design.md`

## Global Constraints

- 운영 main/daemon/SaveTicker 설치본과 주문/계정 설정을 변경하지 않는다. 작업은 새 worktree에 둔다.
- quotes migration16, session migration17. 기존 마이그레이션 수정 금지.
- KR/US·통화·원천시각/수집시각·세션 기준을 분리한다. 누락은0이 아니다.
- quotes 기본30초/신선도120초, 변화율 경계허용오차60초, LLM bundle UTF-8 상한24,000bytes.
- Codex 기본OFF, ON의 다음 새 연구는 Claude bull/Codex bear 및 최소반박1회. 기존결정/주문은 보존한다.
- 실제 모델/API주문 호출 없는 테스트부터 수행한다. 전체 pytest는 동시에1개. 대상 pytest도 공유 lock을 사용한다.
- 작업자는 자기 담당 파일만 변경하고 Git commit은 컨트롤러가 통합해 수행한다.

## Review Focus

- 일부 거래소만 성공한 데이터를 전체 미국 순위라고 오표시하지 않는가.
- 오래된/장마감/미래 자료와 초기화된 누적 거래대금으로 성장률을 만들지 않는가.
- Codex 실패/정책변경/기존 체크포인트가 필수 참여 검증을 우회하지 않는가.
- 최신순 뉴스가 압축 시 버려지거나 핵심 data_gaps가 사라지지 않는가.
- 설정 OFF 및 기존 결정 재개가 기존 동작을 유지하는가.

---

### Task1: KIS broker + quotes persistent market activity

**Files:** broker `market.py/models.py/tr_ids.py` 및 필요한 분리 모듈; quotes `market_activity.py`, `activity_store.py`, `migrations.py`, `ports.py`, `settings.py`, `jobs.py`, `feature.py`, `api.py`, `routes.py`; templates settings/schedule; 해당 tests.

**Produces:** `activity_snapshot(conn, market, *, asof, max_age_s=120)->dict`; `activity_for_ticker(snapshot,ticker)->dict`; `market_activity` port; `GET /quotes/activity?market=KR|US`.

- [ ] 합성응답으로 거래대금/등락률, 거래소병합, 결측/NaN, 실패, 페이지상한 테스트를 작성하고 실패를 확인한다.
- [ ] 공식TR/파라미터를 사용하는 DTO·broker어댑터를 구현한다.
- [ ] asof/partial/stale/리셋/순위탈락/5분비교미준비 테스트를 작성하고 실패를 확인한다.
- [ ] migration16과 제한된30초 collector·저장소·읽기 API/port·설정/템플릿을 구현한다.
- [ ] 해당 tests와 ruff를 통과시키고 변경/근거를 보고한다.

### Task2: LLM input correctness and market activity wiring

**Files:** ingest `ports.py/inputs.py`, saveticker `reader.py`, scan `discovery.py`, regime `market_view.py`, kernel/session `tooling.py`, 필요한 analyst prompt; 해당 tests.

**Consumes:** Task1 quotes API 계약. 이 API가 준비되기 전에는 monkeypatch/fake로 입력 테스트를 작성할 수 있다.

- [ ] 실제 InputsPort 경유 사용자지정 SaveTicker 폴더·뉴스·일정 테스트로 주입누락을 재현한다.
- [ ] 최신순뉴스/오름차순봉/한국어대형입력/24,000bytes 상한 회귀테스트를 작성하고 실패를 확인한다.
- [ ] 설정주입, 품질/시장표시, 압축수정과 수집/축약 metadata를 구현한다.
- [ ] quotes market_activity를 종목/시장/scout 입력에 연결하고 자료없음/미래/시장분리를 테스트한다.
- [ ] 모든 analyst/researcher 전달에 필요한 compact context 계약을 root/Task3와 맞추고 해당 tests를 통과시킨다.

### Task3: Codex research bridge and participation policy

**Files:** kernel `config/__init__.py`, `platform/codex_bin.py`, `session/codex.py`, `session/research_policy.py`, `session/runner.py`, `session/agents.py`, `session/schemas.py`, `session/migrations.py`, `registry/types.py`, 필요 boot migration등록; spot/fo/regime tools/tool_cli/session_kinds/agents; kernel web system routes 및 accounts GETsettings; 해당 tests; templates settings(Codex부분만).

**Produces:** `/settings/codex-debate` GET/PATCH, frozen session policy, validated Codex DebateTurn, provider/call provenance, usage/status 조회.

- [ ] OFF동등성, 최소env/도구차단CLI, JSON/schema오류·timeout·cancel·cap·fake 테스트를 작성하고 실패를 확인한다.
- [ ] migration17·CLI발견/실행·기록·정책고정·read-only bridge를 구현한다.
- [ ] spot/시장/FO ON의독립의견+최소1반박, 합의생략차단, 공급자/입력변경, 위조/누락턴, Claude 정상종료로실패숨기기 방지 테스트를 작성한다.
- [ ] 전용research CLI와 backend 검증·prompt를 연결한다. 기존결정/주문을 삭제하지 않고 다음새연구 적용을 강제한다.
- [ ] WRITE인증/영속ONOFF/실패·사용량 및 GETsettings 확장을 구현하고 해당 tests를 통과시킨다.

### Task4: Web settings, market data and provider visibility

**Files:** web `screens/system/SettingsPanel.tsx`, `screens/data/MarketActivity.tsx`, `screens/data/Data.tsx`, `screens/decision/Decision.tsx`, fixtures/tests; 필요한 backend serialized provider 필드.

- [ ] ON/OFF저장·실패·적용시점·CLI미준비, 시장별가격/거래대금/출처/시각/partial 표시 테스트를 먼저 작성한다.
- [ ] 기존 컴포넌트/스타일로 기능을 구현한다. 구현용 rawJSON이나계정비밀은 표시하지 않는다.
- [ ] typecheck, 관련vitest를 통과시킨다.

### Task5: Integration, review and delivery

- [ ] Task별 spec/품질 리뷰 후 결함을 수정한다. 통합프롬프트가 실제입력키/시장/출처와 맞는지 확인한다.
- [ ] 전체 pytest1개, ruff, web typecheck/vitest/build, registry/generatedSSE검사를 수행한다.
- [ ] 실제주문/생산모델세션 없이 합성CLI·임시데이터 e2e를 확인한다.
- [ ] README/운영안내에 설정위치, 적용범위, 자료제약, 입력감사결과와 검증근거를 기록한다.
- [ ] 독립 전체branch리뷰 후 커밋/PR/CI를 완료한다. 실행중서비스 업데이트와주문경로병합은 기존사용자승인범위에따라 별도판단한다.
