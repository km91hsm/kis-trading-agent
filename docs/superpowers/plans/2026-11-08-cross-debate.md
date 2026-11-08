# 4인 교차 토론 + 분석 제한 시간 조정 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Codex 참여가 켜진 새 연구(spot·market·fo)는 교차 토론 두 판(1판 Claude 강세 ↔ Codex 약세, 2판 Codex 강세 ↔ Claude 약세)을 진행하고 요약에서 합치며, 4인 세션은 제한 시간을 자동으로 늘린다.

**Architecture:** 정책 version 2 가 `lanes` 를 고정하고, 단계 이름은 판 B 전용 접두사(`ResearchSpec.cross_turn_prefix`)로 가른다. 반박·증명·요약은 기존 함수를 판 단위로 돌린 뒤 `debate.merge_lanes()` 가 같은 편을 `DebateTurn` 모양 그대로 합친다. 저장된 version 1 정책은 해시 비교 없이 끝까지 옛 2인 흐름으로 돈다.

**Tech Stack:** Python 3.12·pydantic·typer·SQLite·pytest, React 18·vitest.

**Spec:** `docs/superpowers/specs/2026-11-08-cross-debate-design.md` (구속력 있는 정본 — 이 플랜과 다르면 스펙이 이긴다. 아래 「판정」 절이 스펙의 빈칸을 메운 결정이다)

## Global Constraints

- 브랜치 `feat/cross-debate`(PR #45 `feat/usage-limits` 위에 쌓임). 커밋 작성자는 저장소에 설정된 noreply 그대로(git config 를 바꾸지 않는다). 커밋 메시지는 한국어, 끝에 두 줄:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` / `Claude-Session: https://claude.ai/code/session_018jz1JZn1KDfWHGAgZcWmG5`
- TDD: 실패 테스트 → RED 확인 → 구현 → GREEN. 테스트는 `tmp_path` 만, 네트워크 금지.
- **실제 `claude`·`codex` 를 절대 띄우지 않는다.** Codex 턴은 `runner=` 주입(`tests/kernel/session/test_codex.py` 방식), Claude 세션은 `runner=`/`FAKE_CLAUDE=1`(`features/*/fake.py`). `tests/conftest.py` 의 autouse `_no_real_codex_usage_read` 를 우회하지 않는다.
- Windows CI: 모든 `read_text`/`write_text`/`open` 에 `encoding="utf-8"`. POSIX 전용 검사는 `sys.platform == "win32"` 에서 skip. 파일·폴더 이름에 `:` 금지(시각은 `YYYYMMDD_HHMMSS`, UTC).
- 커널(`src/trader/kernel/`)은 기능 이름(spot·market·fo)을 모른다 — 접두사·에이전트·CLI 는 `ResearchSpec` 으로만.
- 마이그레이션은 추가만, 버전 전역 오름차순. 현재 최댓값 17 → 이 플랜은 **18(kernel/session)**·**19(spot)**. 실행 시 `uv run python -c "from trader.kernel import registry; from trader.kernel.registry import contracts; print(contracts.all_migrations(registry.discover())[-1].version)"` 로 17 인지 다시 확인하고, 다르면 이어지는 번호를 쓴다.
- yaml 키를 더하면 pydantic 모델 + `templates/config/settings.yaml` 을 같이 바꾼다.
- 사용자 대면 문구·docstring·로그는 한국어.
- Codex 약세 PROMPT 는 바이트 단위로 그대로: `codex.digest(codex.PROMPT) == "262b395d3c0e7b5efa63b8b13ca9c6c5e623397fe2e6fb57ad8617387d707319"`.
- 값: 판 B 접두사 spot `20_debatex_r`, market `_market_debatex_r`, fo `_fo_debatex_r`. 판 이름 `"a"`·`"b"`. 필요한 Codex 호출 = `2 × (rounds+1)`. 기본 연장 = `(rounds+1) × (codex_debate.timeout_s + 120)` 초. Bash 제한 = `(timeout_s + 60) × 1000` ms(기본 240000). 대표 stance 경계 ±0.1.
- **알려진 로컬 잡음:** `~/.claude/rate_limits.json` 사용률 ≥ 80% 인 맥에서는 약 11개 테스트(briefing, regime market_view, scalp judge, scan triggers, `kernel/session` 의 research_descriptor·runner codex_policy 등)가 로컬에서만 실패한다. 작업 시작 전에 `uv run pytest -q 2>&1 | tail -15` 로 기준 실패 목록을 적어 두고, 각 태스크 뒤 같은 목록과 비교한다(`git stash` 를 쓰지 말 것 — 스택이 다른 세션과 공유된다). 그 테스트들을 「고치지」 않는다.

## 판정 (스펙이 비워 둔 곳)

1. 대표 stance 는 **평균** 부호 확신 기준(평균 > 0.1 BULLISH, < −0.1 BEARISH), `conviction = |평균|`. 스펙의 「합 > 0.1」은 평균으로 읽는다.
2. 합친 턴의 `valuation_view`·`timing`·`stance_change` 는 **주 턴**(합친 부호 방향으로 부호 확신이 가장 큰 판의 턴, 동률·중립이면 판 a)에서 가져온다. `arguments`·`rebuttals`·`what_would_change_my_mind` 는 a,b 번갈아 중복 제거 5개. `issues`·`responses`·`replies` 는 합친 턴에서 비우고 `by_lane` 에만 둔다(판 간 쟁점 ID 충돌 회피).
3. 판 B 쟁점 ID 는 같은 `bull:`/`bear:` 접두 — 검사는 판 단위, 웹 쟁점 비교는 `lane:issue_id` 로 묶는다.
4. Codex 입력(`payload`)에는 `lane` 을 넣지 않는다(`codex._ALLOWED` 가 걸러 냄). 판 A 약세 입력 해시는 v1 과 같은 모양으로 남는다.
5. 2판이 없는 정책(v1, v2 Codex 꺼짐·건너뜀)에서 판 b 쓰기는 거부한다(같은 모델끼리 두 판 방지). FAKE 는 `configured()` 가 켜짐이면 두 판을 만든다.
6. 도중 실패 표시는 `research_policies` 새 열(마이그레이션 18)에 둔다. 교체는 같은 행 UPDATE 로, `replaced_from_hash`·`replaced_reason` 을 남긴다. 표시는 `CodexError`(실행·한도·상한·출력 검증 실패)일 때만, 입력 조립 실패(`ResearchPolicyError`)는 표시하지 않는다. v1 연구 실패는 표시하지 않는다.
7. market·fo 의 연구 폴더는 시장 폴더(spot 종목 폴더와 같은 곳)라 `_codex_failed_*` 가 종목 목록에 섞인다 → 시장 폴더를 훑는 두 곳(`spot/tools.py` 체크포인트 상태, `spot/decisions_view.py`)이 `_` 로 시작하는 폴더를 건너뛴다.
8. 연장 계산의 `codex_debate.timeout_s` 는 고정 정책 `policy["config"]["timeout_s"]`(연구 시작 때 값)를 쓴다.
9. 런타임 절은 기능 CLI 를 모르므로 「각 흐름의 researcher 묶음 명령에 `--lane b` 를 붙인다」로만 말하고, 정확한 명령은 기능 md(Task 12)가 적는다.
10. 수렴률 표본 정의는 API 필드 `convergence_definition` + evaluation README 한 줄로 남긴다(웹 화면 표시는 하지 않음).

## Review Focus

- **v1/v2 섞인 세션:** 저장된 v1(Codex 켜짐) 미완료 연구가 있는 세션에서 새로 시작하는 연구도 v1 2인으로 시작하고, 서브에이전트에서 bear 가 빠지며 옛 프롬프트 절이 붙는다 — Task 7 `test_session_with_v1_codex_research_runs_legacy_mode`.
- **판 간 쟁점 ID 충돌:** 1판·2판 모두 `bull:growth` 를 쓰면 검사는 판 단위로 통과하고, 웹 쟁점 비교는 두 행으로 따로 보인다 — Task 4 `test_lane_b_turn_error_ignores_lane_a_issues`, Task 11 `issues with same id in two lanes stay separate`.
- **트레이더 묶음 24KB 초과:** `by_lane` 이 붙은 요약도 `trader_summary` 가 판별 턴을 핵심만 남겨 기존 축약 경로 안에 든다 — Task 5 `test_trader_summary_briefs_by_lane_turns`.
- **FAKE_CLAUDE 4턴:** Codex 켜짐 설정 + FAKE 에서 라운드마다 두 판 4턴과 `by_lane` 요약이 생기고, 꺼짐이면 지금처럼 2턴 — Task 6 `test_fake_cross_debate_writes_both_lanes`, Task 9 regime·fo 대응 테스트.
- **Windows 경로 `_codex_failed_<시각>/`:** 이름에 `:` 가 없고(`^_codex_failed_\d{8}_\d{6}(_\d+)?$`), 같은 초에 두 번 실패해도 덮어쓰지 않으며, 시장 폴더에 생겨도 종목으로 보이지 않는다 — Task 8 `test_restart_after_codex_failure_archives_and_switches_to_claude`, `test_archive_name_is_windows_safe_and_unique`, `test_market_dir_listing_skips_underscore_dirs`.

---

### Task 1: 판 B 단계 이름 — `ResearchSpec.cross_turn_prefix` 와 판 인식 정규식

**Files:**
- Modify: `src/trader/kernel/registry/types.py:88-101` (ResearchSpec)
- Modify: `src/trader/features/spot/research.py`, `src/trader/features/regime/research.py`, `src/trader/features/fo/research.py`
- Modify: `src/trader/kernel/session/tooling.py:25` (`DEBATE_RE`), `:72-81` (`load_turns`)
- Modify: `src/trader/features/spot/schemas.py:36-40,94` (`CHECKPOINT_NAMES`, `_STAGE_PATTERNS`)
- Test: `tests/kernel/session/test_tooling.py`, `tests/kernel/session/test_research_descriptor.py`(ResearchSpec 생성에 새 필드), `tests/features/spot/test_schemas.py`

**Interfaces:**
- Produces:
  - `ResearchSpec.cross_turn_prefix: str` — `turn_prefix` 바로 뒤 필수 필드.
  - `ResearchSpec.turn_stage(self, lane: str, round_no: int, side: str) -> str` — `"a"` → `f"{turn_prefix}{round_no}_{side}"`, `"b"` → `f"{cross_turn_prefix}{round_no}_{side}"`, 그 밖 `ValueError`.
  - `tooling.debate_re(turn_prefix: str, cross_turn_prefix: str) -> re.Pattern[str]` — 이름 그룹 `b`(판 B 접두사면 매치), `round`, `side`. 패턴: `^(?:(?P<b>{esc(cross)})|{esc(prefix)})(?P<round>\d+)_(?P<side>bull|bear)$`.
  - `tooling.DEBATE_RE = debate_re("20_debate_r", "20_debatex_r")` (기본값, 커널 docstring 의 기존 규약 그대로).
  - `tooling.parse_debate_stage(pattern: re.Pattern[str], stage: str) -> tuple[str, int, str] | None` — `(lane, round, side)`.
  - `tooling.load_turns(d: Path, pattern: re.Pattern[str] = DEBATE_RE, *, lane: str = "a") -> dict[int, dict[str, DebateTurn]]` — 그 판만.
  - `tooling.load_lanes(d: Path, pattern: re.Pattern[str]) -> dict[str, dict[int, dict[str, DebateTurn]]]` — 턴이 하나라도 있는 판만 키로.
- 기존 호출부 `m.group(1)`/`m.group(2)` (spot tools 599·657, fo tools 71, regime tools 33, spot ledger 31)는 이 태스크에서 `parse_debate_stage` 로 바꾸되 **판 b 는 아직 거부**하지 않아도 된다(판 b 처리는 Task 6·9·10). fo·regime 의 정규식은 `tooling.debate_re(RESEARCH.turn_prefix, RESEARCH.cross_turn_prefix)` 로 만든다.

- [ ] **Step 1: 실패 테스트** (`tests/kernel/session/test_tooling.py`)

```python
def test_debate_re_recognises_both_lanes():
    p = tooling.debate_re("20_debate_r", "20_debatex_r")
    assert tooling.parse_debate_stage(p, "20_debate_r0_bull") == ("a", 0, "bull")
    assert tooling.parse_debate_stage(p, "20_debatex_r2_bear") == ("b", 2, "bear")
    assert tooling.parse_debate_stage(p, "20_debatey_r0_bull") is None
    assert tooling.parse_debate_stage(p, "20_debate_r0_bull.json") is None

def test_load_lanes_splits_by_lane(tmp_path):
    for stage in ("20_debate_r0_bull", "20_debatex_r0_bull"):
        write_checkpoint(tmp_path, stage, checkpoint_payload(ok_turn("bull", 0)))
    lanes = tooling.load_lanes(tmp_path, tooling.DEBATE_RE)
    assert set(lanes) == {"a", "b"} and set(lanes["b"][0]) == {"bull"}
    assert set(tooling.load_turns(tmp_path)) == {0}  # 기본 lane="a"
```

`tests/kernel/session/test_research_descriptor.py` 에 `test_turn_stage_names_lanes`: `spec.turn_stage("b", 1, "bear") == "lunar_cross_r1_bear"`(`cross_turn_prefix="lunar_cross_r"`), `pytest.raises(ValueError)` for `"c"`. 기존 두 `ResearchSpec(...)` 에 `cross_turn_prefix="lunar_cross_r"` 추가.

`tests/features/spot/test_schemas.py`: `schemas.schema_for("20_debatex_r1_bear") is DebateTurn`.

그리고 세 기능 계약: `RESEARCH.turn_stage("b", 0, "bull")` 가 spot `20_debatex_r0_bull`, market `_market_debatex_r0_bull`, fo `_fo_debatex_r0_bull`(`test_research_descriptor.py::test_feature_cross_prefixes`).

- [ ] **Step 2: RED 확인** — `uv run pytest -q tests/kernel/session/test_tooling.py tests/kernel/session/test_research_descriptor.py tests/features/spot/test_schemas.py` → `AttributeError`/`TypeError ... cross_turn_prefix` 로 FAIL.
- [ ] **Step 3: 구현** — 위 Interfaces 그대로. spot `CHECKPOINT_NAMES` 에 `"20_debatex_r<n>_<side>"` 추가, `_STAGE_PATTERNS` 의 토론 패턴은 `^20_debatex?_r\d+_(bull|bear)$`.
- [ ] **Step 4: GREEN + 전체** — 같은 명령 PASS, 이어 `uv run pytest -q` 결과가 기준 실패 목록과 같다.
- [ ] **Step 5: 커밋** — `git commit -m "feat(research): 교차 토론 판 B 단계 이름과 판 인식 정규식"` (+ 트레일러 두 줄)

---

### Task 2: 정책 version 2 — 판(lanes) 고정, v1 보호, 필요 호출 수

**Files:**
- Modify: `src/trader/kernel/session/research_policy.py` (`current`, `start`, `get`, `public_policy`, `status`, 새 헬퍼)
- Modify: `src/trader/kernel/session/runner.py:370` — `research_policy.start(..., version=1)` 을 **명시**(세션 모드 전환은 Task 7. 이 태스크 동안 러너 동작은 바뀌지 않는다)
- Test: `tests/kernel/session/test_research_policy.py`

**Interfaces:**
- Consumes: Task 1 `ResearchSpec.cross_turn_prefix`.
- Produces:
  - `current(conn, settings, *, version: int = 2) -> dict`. `version=1` 은 **지금 함수 출력과 키·값이 같다**(옛 dict). `version=2` 은 v1 키에 더해 `"version": 2`, `"lanes"`, `"prompt_hashes": {"bull": digest(codex.PROMPTS["bull"]), "bear": digest(codex.PROMPTS["bear"])}`(Task 3 전까지는 `PROMPTS` 가 없으므로 이 키는 Task 3 에서 추가 — 이 태스크에서는 생략). 켜짐: `lanes=[{"lane":"a","bull":"claude","bear":"codex"},{"lane":"b","bull":"codex","bear":"claude"}]`, `rounds=max(1, debate_rounds)`. 꺼짐: `lanes=[{"lane":"a","bull":"claude","bear":"claude"}]`. `bull`/`bear` 키는 판 a 값.
  - `lanes(policy: dict) -> list[dict[str, str]]` — `policy["lanes"]` 가 있으면 그것, 없으면(v1) `[{"lane":"a","bull":policy["bull"],"bear":policy["bear"]}]`.
  - `provider_for(policy: dict, lane: str, side: str) -> str | None` — 판이 없으면 None.
  - `codex_slots(policy: dict) -> list[tuple[str, str]]` — 공급자가 codex 인 `(lane, side)`. v2 켜짐 `[("a","bear"),("b","bull")]`.
  - `is_cross(policy: dict) -> bool` — `policy.get("version") == 2 and policy["enabled"] and len(lanes(policy)) == 2`.
  - `start(conn, settings, d, scope, *, now, version: int = 2) -> dict`. 순서: ① 저장된 정책에 `codex_skipped` → 그대로 반환 ② 완료 연구 → 지금과 같은 `resume_completed` 반환 ③ 저장된 정책 `version` 이 없거나 1 → **해시 비교 없이** 그대로 반환 ④ v2 → 지금처럼 해시 비교. 새 정책이 켜짐이면 `codex_allowance(need=len(codex_slots(wanted)) * (rounds+1))` — v2 는 `2×(rounds+1)`, v1 은 `rounds+1`. 건너뜀이면 판 하나 `claude/claude` + `codex_skipped`(v2 는 `lanes` 도 1판으로). `wanted["research"]` 에 `"cross_turn_prefix"` 추가(v2 해시에만 영향).
  - `get()` FAKE 분기: `{**current(conn, settings), "enabled": False, "rounds": settings.debate_rounds, "fake": True}` — v2 라서 설정이 켜짐이면 `lanes` 2개.
  - `public_policy()` → `{"enabled","rounds","bull","bear","version","lanes"}`(`version` 없으면 1).
  - `status()` 에 `"lanes": lanes(current(conn, settings))` 추가(`roles` 유지).

- [ ] **Step 1: 실패 테스트**

```python
def test_v2_policy_has_two_lanes_when_enabled(conn, tmp_path):
    p = start(conn, settings(), tmp_path, SPOT_RESEARCH, now=NOW)
    assert p["version"] == 2 and p["rounds"] == 1
    assert p["lanes"] == [{"lane": "a", "bull": "claude", "bear": "codex"},
                          {"lane": "b", "bull": "codex", "bear": "claude"}]
    assert (p["bull"], p["bear"]) == ("claude", "codex")
    assert rp.codex_slots(p) == [("a", "bear"), ("b", "bull")] and rp.is_cross(p)

def test_v2_disabled_policy_is_single_claude_lane(conn, tmp_path):
    p = start(conn, settings(enabled=False), tmp_path, SPOT_RESEARCH, now=NOW)
    assert p["lanes"] == [{"lane": "a", "bull": "claude", "bear": "claude"}] and not rp.is_cross(p)

def test_stored_v1_policy_reused_without_hash_check(conn, tmp_path):
    old = start(conn, settings(), tmp_path, SPOT_RESEARCH, now=NOW, version=1)
    assert "version" not in old or old["version"] == 1
    again = start(conn, settings(), tmp_path, SPOT_RESEARCH, now=NOW)  # 기본 v2 로 재시작
    assert again["policy_hash"] == old["policy_hash"] and rp.lanes(again) == [
        {"lane": "a", "bull": "claude", "bear": "codex"}]

def test_v1_current_is_byte_identical_to_legacy(conn):
    v1 = rp.current(conn, settings(), version=1)
    assert set(v1) == {"version", "enabled", "rounds", "bull", "bear", "config",
                       "claude_models", "prompt_hash", "schema_hash"} and v1["version"] == 1

def test_cross_needs_two_calls_per_round(conn, tmp_path, monkeypatch):
    _seed_calls(conn, 80 - 3)   # rounds 1 → 필요 4, 남은 3
    p = start(conn, settings(), tmp_path, SPOT_RESEARCH, now=NOW)
    assert not p["enabled"] and "4회 필요" in p["codex_skipped"] and len(p["lanes"]) == 1
```

(`rp` = `research_policy`, `start` = `rp.start`. 기존 `test_skip_when_remaining_calls_cannot_finish_research` 의 필요 수는 v2 기준 `2×(rounds+1)` 로 고친다.)

- [ ] **Step 2: RED 확인** — `uv run pytest -q tests/kernel/session/test_research_policy.py` → 새 테스트 FAIL (`KeyError: 'version'`, `unexpected keyword 'version'`).
- [ ] **Step 3: 구현** — 위 Interfaces.
- [ ] **Step 4: 기존 테스트 정리** — `uv run pytest -q` 에서 **새 기본값 v2 때문에만** 깨지는 기존 테스트(직접 `start()` 를 불러 bear 전용 Codex 증명 흐름을 검사하는 것: `test_research_policy.py`·`test_research_regeneration.py`·`test_research_descriptor.py`·spot/regime/fo 도구 테스트 중 해당분)는 `start(..., version=1)` 로 고정해 **레거시 회귀 테스트**로 남긴다. 단언은 바꾸지 않는다. 기대: 전체 결과가 기준 실패 목록과 같다.
- [ ] **Step 5: 커밋** — `feat(research): 정책 v2 — 교차 두 판 고정, v1 진행 중 연구 보호, 필요 호출 2×(라운드+1)`

---

### Task 3: Codex 턴 측별 PROMPT — `codex.run_turn` 이 bull 도 받는다

**Files:**
- Modify: `src/trader/kernel/session/codex.py:52-68` (PROMPT 아래), `:213-334` (`run_turn`)
- Modify: `src/trader/kernel/session/research_policy.py` (`current(version=2)` 에 `prompt_hashes`)
- Test: `tests/kernel/session/test_codex.py`

**Interfaces:**
- Produces:
  - `codex.PROMPT` 불변(약세). `codex.BULL_PROMPT = PROMPT.replace("독립적인 bear 연구자", "독립적인 bull 연구자", 1)`. `codex.PROMPTS: dict[str, str] = {"bear": PROMPT, "bull": BULL_PROMPT}`.
  - `run_turn` 시그니처 불변. `clean["side"]` 가 `"bull"|"bear"` 이면 받고(그 밖은 기존 오류 문구), 프롬프트·`prompt_hash` 는 `PROMPTS[side]`, 출력 검사 `turn.side != clean["side"]` → `"Codex 턴 side/round 불일치"`, `call.agent = f"researcher-{side}"`.
  - `current(version=2)["prompt_hashes"] == {"bull": digest(BULL_PROMPT), "bear": digest(PROMPT)}`; `prompt_hash`(약세) 키 유지.

- [ ] **Step 1: 실패 테스트**

```python
def test_bear_prompt_bytes_unchanged():
    assert codex.digest(codex.PROMPT) == "262b395d3c0e7b5efa63b8b13ca9c6c5e623397fe2e6fb57ad8617387d707319"
    assert codex.PROMPTS["bear"] is codex.PROMPT
    assert codex.BULL_PROMPT.startswith("당신은 독립적인 bull 연구자입니다.")
    assert codex.BULL_PROMPT.replace("bull 연구자", "bear 연구자", 1) == codex.PROMPT

def test_codex_bull_turn_uses_bull_prompt(conn, monkeypatch):
    # runner 가 input 앞부분과 출력 side 를 확인; ok_turn("bull", 0) 를 쓰게 한다
    ...  # data={..., "side": "bull"} → result["call"]["prompt_hash"] == codex.digest(codex.BULL_PROMPT)
    assert captured["input"].startswith(codex.BULL_PROMPT + "\n")
    assert result["call"]["agent"] == "researcher-bull"

def test_codex_rejects_side_mismatch(conn, monkeypatch):
    # data side="bull" 인데 runner 가 ok_turn("bear", 0) 출력 → CodexError("Codex 턴 side/round 불일치"),
    # research_calls 마지막 행 status == "error"
```

- [ ] **Step 2: RED** — `uv run pytest -q tests/kernel/session/test_codex.py` → `AttributeError: BULL_PROMPT` 등 FAIL.
- [ ] **Step 3: 구현**.
- [ ] **Step 4: GREEN + 전체** — 기존 Codex 테스트(`side="bear"`) 그대로 통과, 전체가 기준과 같다.
- [ ] **Step 5: 커밋** — `feat(codex): 강세 측 Codex 턴(대칭 PROMPT), 약세 PROMPT 해시 유지`

---

### Task 4: 판 단위 검증·생성 — payload / write_error / turn_error / generate_turn / completion_error

**Files:**
- Modify: `src/trader/kernel/session/research_policy.py:305-568`
- Test: `tests/kernel/session/test_research_policy.py`

**Interfaces:**
- Consumes: Task 1 `scope.turn_stage`, Task 2 `lanes/provider_for/codex_slots/is_cross`, Task 3 `codex.PROMPTS`.
- Produces (기본값으로 v1 호출부가 그대로 동작):
  - `write_error(conn, settings, d, scope, *, round_no, side, lane="a") -> str | None` — ① `lane` 이 `lanes(policy)` 에 없으면 `"이 연구에는 {2|1}판이 없습니다"`(FAKE 포함, enabled 검사보다 먼저) ② `not enabled` → None ③ `provider_for(...) == "codex"` → `f"Codex {side}는 전용 도구로만 기록할 수 있습니다: {scope.cli_template}"` ④ 라운드 상한 ⑤ `_bind` ⑥ 같은 판 직전 라운드 양측.
  - `payload(conn, settings, d, scope, round_no, *, side="bear") -> tuple[dict, dict]` — 판 = `side` 가 codex 인 판(`codex_slots`에서; 없으면 `ResearchPolicyError("이 연구에서 Codex {side} 턴은 없습니다")`). `own_previous`/`opponent_previous` 는 그 판의 직전 턴. 데이터 모양은 지금과 같고 `"side": side`.
  - `generate_turn(ctx, d, scope, round_no, *, side="bear", runner=None, bin_path=None, cancel=None) -> dict` — 저장 단계 `scope.turn_stage(lane, round_no, side)`, `call.agent` = `scope.bull_agent`/`scope.bear_agent`, 수령 기록 stage 판 a `f"{scope.scope}:researcher:r{n}:{side}"`(지금과 같음), 판 b `...:{side}:b`. validate 는 `turn_error(..., lane=lane)`. 반환 `{"ok", "path", "provider": "codex", "round", "side", "lane"}`.
  - `turn_error(conn, settings, d, scope, value, *, lane="a")` — 자기·상대 직전 턴을 그 판 접두사로 읽는다(그 밖 규칙 불변).
  - `completion_error(conn, settings, d, scope)` — 판마다 0..rounds × 양측 존재(누락 문구 판 a `f"Codex 필수 토론 미완료: r{n}_{side}"`, 판 b `f"Codex 필수 토론 미완료: 2판 r{n}_{side}"`), 컨텍스트 해시, 판마다 `turn_error(lane=)`, Claude 칸은 `call.provider == "claude"`(문구 `f"Claude {side} 공급자 증명 없음: {판}r{n}"`), Codex 칸은 `research_calls` 행 증명 — `prompt_hash == digest(codex.PROMPTS[side])`, `input_hash == digest(payload(..., side=side)[0])`, `turn.side == side`. v1 정책은 `lanes()` 가 판 a 하나라 지금과 같은 결과.

- [ ] **Step 1: 실패 테스트** (`test_research_policy.py`; 기존 `test_actual_bridge_rounds_complete_and_changed_turn_rejected` 의 `turn()`·`fake()` 를 모듈 헬퍼로 올려 재사용, `scope` 매개변수 spot·market·fo)

```python
def test_cross_rounds_complete_with_two_codex_sides(conn, tmp_path, monkeypatch, scope):
    # v2 start → 라운드 0,1 마다: Claude 판 a bull, Claude 판 b bear 를 write_checkpoint(provider claude),
    # generate_turn(side="bear") 와 generate_turn(side="bull") 를 fake runner 로
    assert out_bear["ok"] and out_bear["lane"] == "a"
    assert out_bull["ok"] and out_bull["lane"] == "b"
    assert read_checkpoint(d, r.turn_stage("b", 1, "bull"))["call"]["agent"] == r.bull_agent
    assert p.completion_error(conn, s, d, r) is None
    # 판 b Codex 강세 턴 변조 → 증명 오류
    write_checkpoint(d, r.turn_stage("b", 0, "bull"), checkpoint_payload({...변경...}, {"provider": "codex"}))
    assert "증명" in p.completion_error(conn, s, d, r)

def test_cross_write_error_enforces_provider_slots(conn, tmp_path):
    # v2 켜짐: (a,bull),(b,bear) 는 None; (a,bear),(b,bull) 는 "Codex" 포함 오류
    # v2 꺼짐 정책: lane="b" → "판이 없습니다"

def test_cross_completion_reports_missing_lane_b(conn, tmp_path):
    # 판 a 만 다 채움 → "2판 r0_bear" 포함 오류   (판 b 약세 Claude 누락)

def test_lane_b_turn_error_ignores_lane_a_issues(conn, tmp_path):
    # 두 판 모두 issue_id "bull:growth"/"bear:rates" 사용, r1 판 b 턴은 판 b r0 쟁점만 응답 → None
    assert p.turn_error(conn, s, d, r, lane_b_r1_bear, lane="b") is None

def test_payload_for_bull_reads_lane_b_previous(conn, tmp_path):
    data, _ = p.payload(conn, s, d, r, 1, side="bull")
    assert data["side"] == "bull" and data["opponent_previous"]["side"] == "bear"
    assert "lane" not in data
```

fake runner 는 `data["side"]` 로 응답 측을 고르고 `kw["input"].startswith(codex.PROMPTS[data["side"]])` 를 단언한다.

- [ ] **Step 2: RED** — `uv run pytest -q tests/kernel/session/test_research_policy.py -k "cross or lane or payload_for_bull"` → FAIL.
- [ ] **Step 3: 구현**.
- [ ] **Step 4: GREEN + 전체** — v1 고정 테스트(Task 2 Step 4)는 그대로 통과. 전체가 기준과 같다.
- [ ] **Step 5: 커밋** — `feat(research): 판 단위 반박·공급자 칸·참여 증명(Codex 강세 포함)`

---

### Task 5: 판 합치기 — `debate.merge_lanes` 와 요약·트레이더 축약

**Files:**
- Modify: `src/trader/kernel/session/debate.py`
- Modify: `src/trader/kernel/session/tooling.py:99-115` (`summarize_turns`), `:547-558` (`trader_summary`)
- Test: `tests/kernel/session/test_debate_merge.py`(새 파일), `tests/kernel/session/test_tooling.py`

**Interfaces:**
- Consumes: Task 1 `tooling.load_lanes`.
- Produces:
  - `debate.merge_turns(turns: list[DebateTurn]) -> DebateTurn` — 판정 1·2. 측·라운드가 다르면 `ValueError`.
  - `debate.merge_lanes(summaries: dict[str, dict], *, evidence_ids: set[str] | None = None) -> dict` — 키가 `{"a"}` 뿐이면 `summaries["a"]` 를 **그대로**(같은 객체) 반환. `{"a","b"}` 면 판별 `round0`/`final` 의 같은 측을 `merge_turns` → `research_summary(merged_r0, merged_final, rounds, evidence_ids=...)` → 덮어쓰기: `sycophantic = any`, `convergence_review = any`, `preferred = "round0" if any(... == "round0") else "final"`; 추가 키 `"lanes": ["a","b"]`, `"by_lane": summaries`. 두 판 `rounds` 가 다르면 `ValueError("두 판의 마지막 라운드가 다릅니다")`.
  - `tooling.summarize_lanes(lanes: dict[str, dict[int, dict[str, DebateTurn]]], *, evidence_ids: set[str] | None = None) -> dict` — 판마다 r0 양측 확인(없으면 `ValueError("라운드 0 의 bull·bear 턴이 모두 필요합니다")`, 판 b 면 문구 앞에 `"2판: "`), 판마다 양측이 있는 마지막 라운드로 `research_summary`, 이어 `merge_lanes`. 판 a 가 없으면 같은 r0 오류.
  - `tooling.summarize_turns(d, out_stage, pattern)` — 내부를 `load_lanes` + `summarize_lanes` 로. 반환에 `"lanes": list(lanes)` 추가.
  - `tooling.trader_summary(summ)` — `by_lane` 이 있으면 각 판의 `round0`/`final` 턴을 `brief_turn` 으로, 판별 `issues` 제거.

- [ ] **Step 1: 실패 테스트** (`test_debate_merge.py`)

```python
def turn(side, stance, c, **kw): return DebateTurn.model_validate(ok_turn(side, 0, stance=stance, conviction=c, **kw))

def test_merge_turns_signed_mean_and_threshold():
    m = merge_turns([turn("bull", "BULLISH", 0.8), turn("bull", "BEARISH", 0.4)])
    assert (m.stance, m.conviction) == ("BULLISH", 0.2)
    m = merge_turns([turn("bull", "BULLISH", 0.3), turn("bull", "BEARISH", 0.2)])
    assert (m.stance, m.conviction) == ("NEUTRAL", 0.05)
    assert m.issues == [] and m.responses == [] and m.replies == []

def test_merge_turns_primary_and_interleave():
    a = turn("bull", "BULLISH", 0.5, arguments=["a1", "a2", "a3"], valuation_view="FAIR")
    b = turn("bull", "BULLISH", 0.9, arguments=["b1", "a1", "b2", "b3"], valuation_view="UNDERVALUED")
    m = merge_turns([a, b])
    assert m.arguments == ["a1", "b1", "a2", "a3", "b2"] and m.valuation_view == "UNDERVALUED"

def test_single_lane_summary_is_unchanged():
    s = research_summary(r0, final, 1)
    assert merge_lanes({"a": s}) is s

def test_warning_flags_are_or_of_lanes():
    out = merge_lanes({"a": calm, "b": sycophantic_lane})
    assert out["sycophantic"] is True and out["preferred"] == "round0"
    assert out["by_lane"]["b"]["sycophantic"] is True and out["lanes"] == ["a", "b"]
    DebateTurn.model_validate(out["final"]["bull"])  # 합친 턴은 DebateTurn 모양
```

`test_tooling.py`:

```python
def test_trader_summary_briefs_by_lane_turns():
    long = ["가" * 600] * 5
    merged = merge_lanes({"a": summ(long), "b": summ(long)})
    out = tooling.trader_summary(merged)
    for lane in ("a", "b"):
        for part in ("round0", "final"):
            for t in out["by_lane"][lane][part].values():
                assert set(t) <= {"side", "round", "stance", "valuation_view", "timing", "conviction"}
    assert len(json.dumps(out, ensure_ascii=False).encode()) < 24_000

def test_summarize_turns_rejects_lane_b_without_r0(tmp_path): ...  # 판 a 완비, 판 b r0 bull 만 → ok False, "2판" 포함
```

- [ ] **Step 2: RED** — `uv run pytest -q tests/kernel/session/test_debate_merge.py tests/kernel/session/test_tooling.py` → `ImportError: merge_lanes`.
- [ ] **Step 3: 구현**.
- [ ] **Step 4: GREEN + 전체**.
- [ ] **Step 5: 커밋** — `feat(debate): 두 판 같은 편 합치기(부호 확신 평균·경고 OR)와 판별 원본 by_lane`

---

### Task 6: spot 도구 — 묶음 `--lane`, 쓰기, `codex-turn --side`, 요약, FAKE 4턴

**Files:**
- Modify: `src/trader/features/spot/research.py` (`cli_template` 끝에 ` --side <bull|bear>`)
- Modify: `src/trader/features/spot/tools.py` — `WRITE_RULES`(78 부근), `checkpoint_write` 583-641, `_name_payload_mismatch` 644-665, `checkpoint_summarize` 670-704, `checkpoint_bundle` 742-783, `codex_turn` 1230
- Modify: `src/trader/features/spot/tool_cli.py:169-177` 와 bundle 명령(`--lane`)
- Modify: `src/trader/features/spot/fake.py:76-100`
- Test: `tests/features/spot/test_tools.py`, `tests/features/spot/test_tool_rules.py`, `tests/features/spot/test_fake.py`

**Interfaces:**
- Consumes: Task 1·2·4·5.
- Produces:
  - `WRITE_RULES` 에 `StageRule("20_debatex_r*_*", requires=("00_inputs",), min_counts=(("10_analyst_*", 2),))`.
  - `checkpoint_write` — `parse_debate_stage` 로 `(lane, rnd, side)`, `write_error(..., lane=lane)`, `turn_error(..., lane=lane)`.
  - `checkpoint_bundle(ctx, *, ticker, market, date, kind, round=None, side=None, lane: str = "a")` — `lane ∉ {"a","b"}` → `"--lane 은 a|b 입니다"`; 정책 `lanes` 에 없는 판 → `"이 연구에는 2판이 없습니다"`. 직전 턴은 `tooling.load_turns(d, _DEBATE_RE, lane=lane)`. 묶음 data 에 `"lane": lane`. 수령 stage 판 b `f"researcher:r{round}:{side}:b"`.
  - `codex_turn(ctx, *, ticker, market, date, round, side: str = "bear")` → `generate_turn(..., side=side)`. CLI `--side` 기본 `"bear"`, bundle CLI `--lane` 기본 `"a"`.
  - `checkpoint_summarize` — `tooling.load_lanes` + `tooling.summarize_lanes(evidence_ids=...)`; `ValueError` → `{"ok": False, "error": str(e)}`. 반환에 `"lanes"`.
  - `fake.run_fake_ticker` — `research_policy.lanes(research_policy.get(ctx.conn, ctx.settings, rd, RESEARCH))` 의 판마다 `RESEARCH.turn_stage(lane, n, side)` 로 쓴다(agent `f"researcher-{side}"`).

- [ ] **Step 1: 실패 테스트**

```python
# test_tools.py
def test_bundle_lane_b_uses_lane_b_previous(...):  # v2 켜짐 정책, 판 a·b r0 저장
    out = tools.checkpoint_bundle(ctx, ..., kind="researcher", round=1, side="bear", lane="b")
    assert out["ok"] and out["data"]["lane"] == "b"
    assert out["data"]["opponent_previous"] == lane_b_r0_bull   # 판 a 강세가 아니라 판 b 강세

def test_write_rejects_claude_in_codex_slot_lane_b(...):
    out = tools.checkpoint_write(ctx, ..., stage="20_debatex_r0_bull", payload=ok_turn("bull", 0))
    assert not out["ok"] and "Codex" in out["error"]

def test_write_accepts_claude_lane_b_bear(...):
    assert tools.checkpoint_write(ctx, ..., stage="20_debatex_r0_bear", payload=turn_with_issues("bear", 0))["ok"]

def test_bundle_lane_b_rejected_without_cross_policy(...):  # v2 꺼짐
    assert "2판" in tools.checkpoint_bundle(ctx, ..., kind="researcher", round=0, side="bull", lane="b")["error"]

def test_codex_turn_side_passthrough(monkeypatch, ...):
    # research_policy.generate_turn 을 가로채 side 인자 확인
    tools.codex_turn(ctx, ticker="005930", market="KR", date=D, round=0, side="bull")
    assert seen["side"] == "bull"

def test_summarize_merges_two_lanes(...):  # FAKE 아닌 v2 꺼짐 + 직접 write_checkpoint 로 두 판 (검증 우회)
    out = tools.checkpoint_summarize(ctx, ...)
    assert out["ok"] and read_checkpoint(d, "25_research_summary")["data"]["lanes"] == ["a", "b"]

# test_fake.py
def test_fake_cross_debate_writes_both_lanes(monkeypatch, ...):
    monkeypatch.setenv("FAKE_CLAUDE", "1")   # settings codex_debate.enabled=True, debate_rounds=1
    written = run_fake_ticker(...)
    assert {f"20_debatex_r{n}_{s}" for n in (0, 1) for s in ("bull", "bear")} <= set(written)
    assert "by_lane" in read_checkpoint(rd, "25_research_summary")["data"]

def test_fake_without_codex_writes_single_lane(...):
    assert not any(s.startswith("20_debatex_") for s in written)
```

`test_tool_rules.py`: `first_rule(WRITE_RULES, "20_debatex_r0_bull")` 가 None 이 아니다.

- [ ] **Step 2: RED** — `uv run pytest -q tests/features/spot/test_tools.py tests/features/spot/test_tool_rules.py tests/features/spot/test_fake.py` → FAIL.
- [ ] **Step 3: 구현**.
- [ ] **Step 4: GREEN + 전체**.
- [ ] **Step 5: 커밋** — `feat(spot): 교차 토론 2판 도구 — 묶음 --lane, codex-turn --side, 요약 합치기, FAKE 4턴`

---

### Task 7: 러너 — 세션 모드, 서브에이전트, 런타임 절, 제한 시간 연장, Bash 제한

**Files:**
- Modify: `src/trader/kernel/config/__init__.py:82-107` (`LlmSettings.cross_debate_extra_s`)
- Modify: `templates/config/settings.yaml:111` 아래에 `cross_debate_extra_s: null   # 4인 교차 토론 연구마다 더할 초(0~3600). 비우면 (라운드+1)×(codex_debate.timeout_s+120) 자동`
- Modify: `src/trader/kernel/session/research_policy.py` (`session_version`, `cross_extra_s`)
- Modify: `src/trader/kernel/session/runner.py:365-385, 418-482, 488-495, 527`
- Test: `tests/kernel/session/test_runner.py`, `tests/kernel/session/test_research_policy.py`, `tests/kernel/config/test_templates.py`(기존 로드 테스트가 새 키를 받는지)

**Interfaces:**
- Consumes: Task 2 `start(version=)`, `is_cross`, `lanes`.
- Produces:
  - `LlmSettings.cross_debate_extra_s: int | None = Field(None, ge=0, le=3600)`.
  - `research_policy.session_version(conn, targets: list[tuple[Path, ResearchSpec]]) -> int` — 저장된 정책 중 하나라도 (version 1 이고 `enabled` 이고 `completed_stage` 미완료)면 1, 아니면 2.
  - `research_policy.cross_extra_s(settings, policy: dict) -> int` — `is_cross` 아니면 0; 설정값이 정수면 그 값; None 이면 `(policy["rounds"] + 1) * (policy["config"]["timeout_s"] + 120)`.
  - `runner.codex_section(research: list[tuple[Path, ResearchSpec, dict]], *, cross: bool) -> str` — `cross=False` 는 지금 문자열과 같다(리팩터만). `cross=True` 는 판 두 개 기준: 「1판: 강세 = `{bull_agent}`(Claude), 약세 = `{tool} {cli_template}` 의 `--side bear`」, 「2판: 강세 = 같은 도구 `--side bull`, 약세 = `{bear_agent}`(Claude), Claude 2판 묶음은 각 흐름의 researcher 묶음 명령에 `--lane b`, 저장은 판 B 단계 이름 `{cross_turn_prefix}<n>_<side>`」, 「라운드마다 4턴, 같은 라운드 4턴은 직전 라운드만 보므로 Claude 두 턴은 병렬, Codex 두 턴은 Bash 로 하나씩(각 최대 `codex_debate.timeout_s`)」, 「네 턴이 모두 끝나야 다음 라운드, 마지막에 summarize」, 기존 쟁점·반박·WTI 문단과 「Codex 오류/상한/미완료면 중단하고 오류를 보고」 유지. 「Claude bear를 쓰지 않는다」 문구는 cross 에 없다.
  - `runner.bash_timeout_ms(settings, policies: list[dict]) -> int` — `(max(timeout_s) + 60) * 1000`, timeout_s 후보는 켜진 정책들의 `config.timeout_s` + `settings.llm.codex_debate.timeout_s`.
  - `run_session`: `version = research_policy.session_version(conn, targets)` → `start(..., version=version)`. `cross = any(is_cross(p))`. 에이전트 제거는 `codex_required and not cross` 일 때만(옛 동작). `timeout = settings.llm.timeout_for(kind.timeout_key) + sum(cross_extra_s(settings, p) for p in policies)`. env 에 `"BASH_DEFAULT_TIMEOUT_MS"`·`"BASH_MAX_TIMEOUT_MS"` = `str(bash_timeout_ms(...))` (FAKE 아닌 모든 세션).

- [ ] **Step 1: 실패 테스트** (`test_runner.py`, 기존 `test_codex_policy_missing_turns_fail_even_when_claude_exits_success` 패턴: `make_ctx`, `fake(cmd, **kw)` 가 prompt·agents·`kw["timeout"]`·`kw["env"]` 캡처)

```python
def test_cross_session_keeps_bear_agent_and_extends_timeout(project, conn, monkeypatch):
    # LlmSettings(codex_debate={"enabled": True}), debate_rounds 1, timeout_s 900
    assert "researcher-bear" in captured["agents"]
    assert all(s in captured["prompt"] for s in ("1판", "2판", "--lane b", "--side bull", "codex-turn"))
    assert "Claude bear를 쓰지 않는다" not in captured["prompt"]
    assert captured["timeout"] == 900 + 600
    assert captured["env"]["BASH_DEFAULT_TIMEOUT_MS"] == captured["env"]["BASH_MAX_TIMEOUT_MS"] == "240000"
    assert not result.ok and "Codex" in result.error

def test_market_cross_timeout_is_1200(...): ...      # regime MARKET kind → 600 + 600
def test_cross_extra_override_and_rounds_two(...):   # cross_debate_extra_s=300 → +300; None·rounds 2 → +900
def test_disabled_codex_session_has_no_extra(...):   # timeout == 900, BASH_* 는 "240000"

def test_session_with_v1_codex_research_runs_legacy_mode(project, conn, monkeypatch):
    # 미리 start(conn, s, d, SPOT RESEARCH, now=..., version=1) 로 v1 켜짐 정책 저장 → run_session
    assert "researcher-bear" not in captured["agents"]
    assert "Claude bear를 쓰지 않는다" in captured["prompt"]
    assert captured["timeout"] == 900
```

`test_research_policy.py`: `test_session_version_picks_legacy_only_for_unfinished_v1_codex`(v1 켜짐 미완료 → 1, 같은 정책 + `30_decision` → 2, v1 꺼짐 → 2, 없음 → 2).

기존 `test_codex_policy_missing_turns_fail_even_when_claude_exits_success` 는 v1 정책을 미리 저장해 레거시 단언을 그대로 유지한다(단언 불변).

- [ ] **Step 2: RED** — `uv run pytest -q tests/kernel/session/test_runner.py -k "cross or legacy or extra or disabled_codex" tests/kernel/session/test_research_policy.py -k session_version` → FAIL.
- [ ] **Step 3: 구현**.
- [ ] **Step 4: GREEN + 전체** — `tests/kernel/config/test_templates.py` 포함 전체가 기준과 같다.
- [ ] **Step 5: 커밋** — `feat(runner): 4인 교차 세션 — 두 서브에이전트 유지·판 두 개 절·제한 시간 자동 연장·Bash 제한`

---

### Task 8: 도중 Codex 실패 표시와 Claude 2인 재시작

**Files:**
- Modify: `src/trader/kernel/session/migrations.py` (새 `RESEARCH_FAILOVER = Migration(18, "research-failover", ...)`)
- Modify: `src/trader/kernel/registry/contracts.py:30` (`KERNEL_MODULES` 끝에 추가)
- Modify: `src/trader/kernel/session/research_policy.py` (`mark_codex_failed`, `start` 의 교체 분기, `generate_turn` 의 표시)
- Modify: `src/trader/features/spot/tools.py:1216`, `src/trader/features/spot/decisions_view.py:504` — `p.name.startswith("_")` 건너뜀
- Test: `tests/kernel/session/test_research_failover.py`(새 파일), `tests/features/spot/test_tools.py`, `tests/features/spot/test_decisions_web.py`

**Interfaces:**
- Consumes: Task 2 `start` 순서, Task 4 `generate_turn`.
- Produces:
  - 마이그레이션 18 statements: `ALTER TABLE research_policies ADD COLUMN codex_failed_at TEXT`, `... codex_failed_reason TEXT`, `... replaced_from_hash TEXT`, `... replaced_reason TEXT`.
  - `research_policy.mark_codex_failed(conn, d: Path, scope: ResearchSpec, *, reason: str, now: datetime) -> None` — `UPDATE research_policies SET codex_failed_at=?, codex_failed_reason=? WHERE research_key=?`.
  - `generate_turn` — `codex.CodexError` 를 잡았고 `is_cross(policy)` 면 `mark_codex_failed(reason=str(exc))` 뒤 지금처럼 `{"ok": False, "error"}`.
  - `start` — Task 2 순서의 ②(완료)와 ③(v1) 사이: 저장 정책이 `is_cross` 이고 `codex_failed_at` 이 있으면
    1. `archive = _archive_dir(d, now)` — `research_policy._archive_dir(d: Path, now: datetime) -> Path` 는 `d / f"_codex_failed_{now.astimezone(UTC):%Y%m%d_%H%M%S}"` 를 돌려주고, 이미 있으면 `_2`, `_3`… 접미를 붙인 첫 빈 이름(폴더는 만들어서 돌려준다).
    2. `list_checkpoints(d)` 중 `scope.turn_prefix`·`scope.cross_turn_prefix` 로 시작하는 것과 `scope.summary_stage` 의 `.json` 을 `Path.replace` 로 옮긴다(애널리스트·입력은 그대로).
    3. 새 정책 = `current(conn, settings, version=2)` 를 Claude 1판으로: `enabled False`, `rounds = settings.debate_rounds`, `lanes=[a claude/claude]`, `bear "claude"`, `codex_skipped = f"Codex 도중 실패 — {reason} — 이번 분석은 Claude 강세·약세"`, `research` 키 포함.
    4. `UPDATE research_policies SET policy_json=?, policy_hash=?, context_hash=NULL, codex_failed_at=NULL, replaced_from_hash=<옛 해시>, replaced_reason=<사유>` 후 새 정책 반환. 이후 재시작은 ①(`codex_skipped`)에서 그대로 반환.
  - `_stored` 는 바꾸지 않는다. 실패 열은 별도 조회 `_failure(conn, d, scope) -> tuple[str, str] | None`(`sqlite3.OperationalError` → None).

- [ ] **Step 1: 실패 테스트** (`test_research_failover.py`)

```python
def test_cross_codex_failure_marks_policy(conn, tmp_path, monkeypatch):
    # v2 start, seed, runner 가 returncode 1 → generate_turn ok False
    row = conn.execute("SELECT codex_failed_at, codex_failed_reason FROM research_policies").fetchone()
    assert row[0] and "비정상 종료" in row[1]

def test_v1_codex_failure_is_not_marked(conn, tmp_path, monkeypatch):  # start(version=1) → 열이 NULL

def test_restart_after_codex_failure_archives_and_switches_to_claude(conn, tmp_path, monkeypatch, scope):
    # 판 a r0 bull, 판 b r0 bear, summary 를 써 두고 실패 표시 → start 다시
    p2 = rp.start(conn, s, d, r, now=NOW)
    assert not p2["enabled"] and p2["codex_skipped"].startswith("Codex 도중 실패")
    assert rp.lanes(p2) == [{"lane": "a", "bull": "claude", "bear": "claude"}]
    assert not any(x.startswith((r.turn_prefix, r.cross_turn_prefix)) for x in list_checkpoints(d))
    assert r.summary_stage not in list_checkpoints(d)
    assert any(x.startswith(r.report_prefix) for x in list_checkpoints(d))   # 애널리스트 보존
    (archive,) = [x for x in d.iterdir() if x.is_dir() and x.name.startswith("_codex_failed_")]
    assert (archive / f"{r.turn_stage('b', 0, 'bear')}.json").is_file()
    row = conn.execute("SELECT replaced_from_hash, replaced_reason, codex_failed_at FROM research_policies").fetchone()
    assert row[0] == p1["policy_hash"] and row[1] and row[2] is None
    assert rp.start(conn, s, d, r, now=NOW)["policy_hash"] == p2["policy_hash"]   # 세 번째 시작은 그대로

def test_archive_name_is_windows_safe_and_unique(conn, tmp_path):
    a1, a2 = rp._archive_dir(d, NOW), rp._archive_dir(d, NOW)
    assert a1 != a2 and a1.is_dir() and a2.is_dir()
    names = sorted(x.name for x in d.iterdir() if x.is_dir())
    assert all(re.fullmatch(r"_codex_failed_\d{8}_\d{6}(_\d+)?", n) for n in names) and len(names) == 2
    assert all(":" not in n for n in names)

def test_policy_change_still_rejected_without_failure(conn, tmp_path):  # 표시 없음 + 설정 변경 → ResearchPolicyError
```

`test_tools.py::test_market_dir_listing_skips_underscore_dirs`: 시장 폴더에 `_codex_failed_20261108_010203/` 와 `005930/` → 체크포인트 상태 도구의 `tickers` 키가 `["005930"]`. `test_decisions_web.py` 에 같은 조건으로 decisions_view 목록에 `_codex_failed_*` 가 없음.

- [ ] **Step 2: RED** — `uv run pytest -q tests/kernel/session/test_research_failover.py tests/features/spot/test_tools.py -k underscore tests/features/spot/test_decisions_web.py` → FAIL(`no such column: codex_failed_at` 등).
- [ ] **Step 3: 구현** — 마이그레이션 번호를 Global Constraints 명령으로 다시 확인.
- [ ] **Step 4: GREEN + 전체** — `tests/kernel/ledger`·`tests/kernel/registry` 계약 테스트 포함 기준과 같다.
- [ ] **Step 5: 커밋** — `feat(research): Codex 도중 실패 표시 → 재시작 때 토론 보존 후 Claude 2인으로 교체`

---

### Task 9: market·fo 대응

**Files:**
- Modify: `src/trader/features/regime/research.py`, `src/trader/features/fo/research.py` (`cli_template` 에 ` --side <bull|bear>`)
- Modify: `src/trader/features/regime/tools.py:33-34, 110-134, 160-221, 278-284`, `src/trader/features/regime/tool_cli.py` (bundle `--lane`, codex-turn `--side`), `src/trader/features/regime/fake.py:55-70`
- Modify: `src/trader/features/fo/tools.py:71-88, 298-316, 370-441, 928-941`, `src/trader/features/fo/fake.py:47`, `src/trader/features/fo/session.py:42`
- Test: `tests/features/regime/test_market_tools.py`, `tests/features/fo/test_tools.py`, `tests/features/fo/test_fake.py`, regime fake 테스트가 있는 파일(`tests/features/regime/test_regime.py` 또는 `test_market_view.py` — `grep -n run_fake tests/features/regime`)

**Interfaces:**
- Consumes: Task 1·2·4·5·6 과 같은 함수(spot 과 같은 모양).
- Produces:
  - regime `_MARKET_DEBATE_RE = tooling.debate_re(RESEARCH.turn_prefix, RESEARCH.cross_turn_prefix)`, `_MARKET_WRITABLE` 에 `"_market_debatex_r<n>_<bull|bear>"`. `bundle(..., lane: str = "a")`, `codex_turn(ctx, *, market, date, round, side: str = "bear")`, 쓰기 `write_error/turn_error(lane=)`, summarize 는 Task 5 `summarize_turns`(판 합치기 포함).
  - fo `_FO_DEBATE_RE` 동일 방식, `_FO_WRITABLE` 추가, `FO_WRITE_RULES` 에 `StageRule("_fo_debatex_r*_*", requires=(FO_INPUTS,))`. `FO_SUBMIT_RULE` 은 판 a r0 그대로. `codex_turn(ctx, *, date, round, side="bear")`, bundle `lane="a"`. `fo/session.py:42` 체크포인트 이름 목록에 `"_fo_debatex_r<n>_<bull|bear>"`.
  - 두 fake: `research_policy.lanes(get(...))` 의 판마다 기존 라운드 범위로 `RESEARCH.turn_stage(lane, n, side)`.

- [ ] **Step 1: 실패 테스트** — spot Task 6 의 여섯 개를 regime·fo 에 맞춰(이름 접두 `test_market_`·`test_fo_`): `..._bundle_lane_b_uses_lane_b_previous`, `..._write_rejects_claude_in_codex_slot_lane_b`, `..._codex_turn_side_passthrough`, `..._summarize_merges_two_lanes`, `..._fake_cross_debate_writes_both_lanes`(market: `_market_debatex_r0_bull` 등, fo: `_fo_debatex_r0_bull`·`_fo_debatex_r0_bear`), `..._fake_without_codex_writes_single_lane`.
- [ ] **Step 2: RED** — `uv run pytest -q tests/features/regime tests/features/fo -k "lane or cross or side_passthrough or single_lane"` → FAIL.
- [ ] **Step 3: 구현**.
- [ ] **Step 4: GREEN + 전체**.
- [ ] **Step 5: 커밋** — `feat(regime,fo): 시장·파생 판단도 교차 토론 2판`

---

### Task 10: 원장 `debate_turns.lane` · 결정 상세 · 수렴률 판 단위

**Files:**
- Modify: `src/trader/features/spot/migrations.py` (`MIGRATIONS` 끝에 `Migration(19, "spot-debate-lane", ...)`)
- Modify: `src/trader/features/spot/ledger.py:31, 84-100`
- Modify: `src/trader/features/spot/decisions_view.py:113-127, 386-392`
- Modify: `src/trader/features/evaluation/metrics.py:37-58`, `src/trader/features/evaluation/performance.py:329`, `src/trader/features/evaluation/README.md:32`
- Test: `tests/features/spot/test_ledger.py`, `tests/features/spot/test_decisions_web.py`, `tests/features/evaluation/test_metrics.py`, `tests/features/evaluation/test_performance.py`

**Interfaces:**
- Produces:
  - 마이그레이션 19 statements(순서대로): `CREATE TABLE debate_turns_v19 (... 기존 열 ..., lane TEXT NOT NULL DEFAULT 'a', UNIQUE(decision_date, market, ticker, lane, round, side))`, `INSERT INTO debate_turns_v19(id, decision_date, market, ticker, lane, round, side, stance, conviction, data_json, created_at) SELECT id, decision_date, market, ticker, 'a', round, side, stance, conviction, data_json, created_at FROM debate_turns`, `DROP TABLE debate_turns`, `ALTER TABLE debate_turns_v19 RENAME TO debate_turns`. (`grep -n "ON debate_turns" src` 로 인덱스가 없음을 확인 — 있으면 다시 만든다.)
  - `ledger._DEBATE_RE = tooling.DEBATE_RE`; ingest 는 `parse_debate_stage` 의 lane 을 넣고 `ON CONFLICT(decision_date, market, ticker, lane, round, side)`.
  - `_debate_row` 출력에 `"lane"`, 체크포인트는 `RESEARCH.turn_stage(row["lane"], row["round"], row["side"])`. 조회 `ORDER BY lane, round, side`.
  - `metrics.CONVERGENCE_DEFINITION = "판(강세·약세 한 쌍) 단위 — 4인 교차 토론 결정은 표본 2개"`; `convergence_rate` 의 키에 `lane` 추가. performance 결과에 `"convergence_definition": metrics.CONVERGENCE_DEFINITION`.

- [ ] **Step 1: 실패 테스트**

```python
# test_ledger.py
def test_lane_migration_backfills_a(tmp_path):
    migs = contracts.all_migrations(registry.discover())
    conn = connect(tmp_path / "t.db"); init_schema(conn, [m for m in migs if m.version < 19])
    conn.execute("INSERT INTO debate_turns(decision_date,market,ticker,round,side,stance,conviction,data_json,created_at) VALUES('2026-11-08','KR','005930',0,'bull','BULLISH',0.7,'{}','t')")
    init_schema(conn, migs)
    assert conn.execute("SELECT lane FROM debate_turns").fetchone()[0] == "a"

def test_ingest_lane_b_keeps_both_rows(conn):
    ingest_checkpoint(conn, D, "KR", "005930", "20_debate_r0_bull", cp_bull_a)
    ingest_checkpoint(conn, D, "KR", "005930", "20_debatex_r0_bull", cp_bull_b)
    assert [r[0] for r in conn.execute("SELECT lane FROM debate_turns ORDER BY lane")] == ["a", "b"]

# test_decisions_web.py
def test_trace_debate_rows_carry_lane_and_provider(...):
    # 판 b 강세 체크포인트 call.provider="codex" → 행 {"lane": "b", "side": "bull", "provider": "codex"}

# test_metrics.py
def test_convergence_counts_each_lane(conn):
    # 한 결정: 판 a 최종 gap 0.1(수렴), 판 b 최종 gap 0.9 → 0.5
    assert convergence_rate(conn, days=30, now=NOW) == 0.5
```

`test_performance.py`: 결과 dict 에 `convergence_definition` 키.

- [ ] **Step 2: RED** — `uv run pytest -q tests/features/spot/test_ledger.py tests/features/spot/test_decisions_web.py tests/features/evaluation/test_metrics.py tests/features/evaluation/test_performance.py` → FAIL.
- [ ] **Step 3: 구현** — 번호 19 를 다시 확인(Task 8 이 18).
- [ ] **Step 4: GREEN + 전체**.
- [ ] **Step 5: 커밋** — `feat(ledger): 토론 턴 판(lane) 열·결정 상세 판 표시·수렴률 판 단위`

---

### Task 11: 웹 — 결정 상세 판·공급자, 쟁점 비교 판 구분, Codex 카드 역할

**Files:**
- Modify: `web/src/lib/trace.ts:10, 52-60` (`TraceDebate.lane`, normalize, 토론 단계 줄)
- Modify: `web/src/screens/decision/Decision.tsx:63-71`
- Modify: `web/src/screens/decision/DebateComparison.tsx:9-45`
- Modify: `web/src/screens/system/CodexDebate.tsx:17` 와 역할 표시부
- Modify: `web/src/lib/glossary.ts:85-88` 근처 (`"debate.lane"`: `{label: "판", help: "1판 = Claude 강세 ↔ Codex 약세, 2판 = Codex 강세 ↔ Claude 약세"}`)
- Test: `web/src/lib/__tests__/trace.test.ts`, `web/src/screens/__tests__/decision.test.tsx`, `web/src/screens/__tests__/decision-evidence.test.tsx`, `web/src/screens/__tests__/codex-settings.test.tsx`

**Interfaces:**
- Consumes: Task 10 API 행 `lane: "a"|"b"`, Task 2 `status().lanes: {lane, bull, bear}[]`.
- Produces:
  - `TraceDebate.lane?: string | null` (없으면 `"a"` 로 취급). `laneLabel(lane) = lane === "b" ? "2판" : "1판"`(trace.ts export).
  - 토론 표: 첫 열 「판」(`laneLabel`), 「측」 칩 텍스트 `${강세|약세} · ${Claude|Codex|미기록}`, 정렬 판 → 라운드 → 측. 「참여 모델」 열은 모델만.
  - 쟁점 비교: 쟁점 키 `${lane}:${issue_id}`, 참여자 `${laneLabel} ${공급자} (${sideLabel})`, 확신 행 키 `${lane}:${side}:${provider}:${model}`.
  - `traceSteps` 토론 줄: 판 b 턴이 있을 때만 `r1: 1판 강세 … · 1판 약세 … · 2판 강세 … · 2판 약세 …`, 없으면 지금 문구 그대로.
  - Codex 카드: `lanes` 가 2개면 「1판 Claude 강세 · Codex 약세 / 2판 Codex 강세 · Claude 약세」, 아니면 지금 `roles` 표시.

- [ ] **Step 1: 실패 테스트** (vitest)

```ts
// decision.test.tsx
it("debate table shows lane and provider per side", async () => {
  // debate: [{round:0, side:"bull", lane:"a", provider:"claude"}, {round:0, side:"bull", lane:"b", provider:"codex"}]
  expect(text).toContain("2판"); expect(text).toContain("강세 · Codex"); expect(text).toContain("강세 · Claude");
});
// decision-evidence.test.tsx
it("issues with same id in two lanes stay separate", async () => {
  // 판 a·b 모두 issue_id "bull:growth", topic 서로 다름 → 두 topic 모두 보이고 쟁점 행 2개
});
// trace.test.ts
it("debate step names lanes only when lane b exists", () => {
  expect(traceSteps(withLaneB).find(s => s.key === "debate")!.lines[0]).toContain("2판 강세");
  expect(traceSteps(base).find(s => s.key === "debate")!.lines[0]).not.toContain("판");
});
// codex-settings.test.tsx
it("shows both lanes when cross debate is on", async () => {
  expect(text).toContain("1판 Claude 강세 · Codex 약세"); expect(text).toContain("2판 Codex 강세 · Claude 약세");
});
```

- [ ] **Step 2: RED** — `cd web && npm test -- --run` → 새 테스트 FAIL.
- [ ] **Step 3: 구현**.
- [ ] **Step 4: GREEN** — `cd web && npm run typecheck && npm test -- --run && npm run build` 모두 통과.
- [ ] **Step 5: 커밋** — `feat(web): 결정 상세 판·공급자 표시, 쟁점 비교 판 구분, Codex 카드 두 판 역할`

---

### Task 12: 에이전트 프롬프트 md · 문서

**Files:**
- Modify: `src/trader/features/spot/agents/orchestrator.md:24-26`(d·e 에 「Codex 교차 토론이면 2판 묶음은 같은 bundle 명령에 `--lane b`, 저장 단계는 `20_debatex_r<n>_<side>`」), `researcher-bull.md`·`researcher-bear.md` 의 「Codex와 대칭 토론」 절(입력 `lane` 이 `"b"` 면 상대가 Codex 강세/약세; 쟁점 ID 규칙은 같음), `trader.md:11-13`
- Modify: `src/trader/features/regime/agents/market-orchestrator.md:16`, `market-researcher-bull.md`·`market-researcher-bear.md`, `strategist.md:11-12`
- Modify: `src/trader/features/fo/agents/fo-orchestrator.md:16`, `fo-trader.md:11-12`, fo researcher md 가 있으면 같은 절
- Modify: `src/trader/features/spot/README.md:7`, `src/trader/features/regime/README.md:14`, `src/trader/features/fo/README.md:68-74`, `docs/user-guide.md`(Codex 토론 절: 두 판·자동 연장·도중 실패 시 Claude 재시작·`cross_debate_extra_s` 를 첫 며칠 실측 뒤 다시 정한다), `docs/market-data-codex.md`(역할 표)
- Test: `tests/kernel/session/test_agents.py`(이미 있는 md 로드·스키마 계약이 통과하는지 — 새 테스트 하나)

**Interfaces:**
- Consumes: Task 5 `by_lane`, Task 6·9 CLI(`--lane`, `--side`), 단계 이름.
- Produces: 트레이더·전략가·fo 트레이더 md 의 한 단락(세 곳 같은 뜻): 「`research_summary` 의 강세·약세는 각각 Claude·Codex 두 의견을 합친 것이다(`lanes: ["a","b"]`). 두 판의 의견이 갈리면 `by_lane` 을 참고한다. `sycophantic`·`convergence_review` 는 한 판이라도 켜지면 켜진다.」 입력 스키마 줄에 `lanes, by_lane` 추가.

- [ ] **Step 1: 실패 테스트** — `test_agents.py::test_trader_prompts_mention_by_lane`: spot `trader.md`, regime `strategist.md`, fo `fo-trader.md` 본문에 `"by_lane"` 포함, spot·market·fo 오케스트레이터 md 에 `"--lane b"` 포함(경로는 `importlib.resources` 또는 기존 테스트의 md 로딩 헬퍼로, `read_text(encoding="utf-8")`).
- [ ] **Step 2: RED** — `uv run pytest -q tests/kernel/session/test_agents.py -k by_lane` → FAIL.
- [ ] **Step 3: md·문서 수정**.
- [ ] **Step 4: GREEN + 전체 + 린트** — `uv run pytest -q`(기준과 같음), `uv run ruff check src tests` → `All checks passed!`, `cd web && npm run typecheck && npm test -- --run && npm run build` 통과, `uv run trader dev gen-sse-keys --check`·`uv run trader dev gen-nav --check` 통과(이 플랜은 생성 파일을 바꾸지 않는다).
- [ ] **Step 5: 커밋** — `docs: 교차 토론 두 판 — 에이전트 프롬프트·기능 README·사용자 설명서`
