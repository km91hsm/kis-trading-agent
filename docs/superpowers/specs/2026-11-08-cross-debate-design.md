# 4인 교차 토론 + 분석 제한 시간 조정 (설계)

날짜: 2026-11-08 · 크기: 프로젝트 · 선행: PR #45(사용량 조절·호출 제한, `feat/usage-limits`) 위에서 작업

## 의도
지금 Codex 토론 참여가 켜지면 연구 토론은 Claude 강세 대 Codex 약세 한 판이다. 사용자는 Claude 와 Codex 가 **각각 강세·약세를 모두** 맡기를 원한다(「클로드도 강세·약세, 코덱스도 강세·약세」). 목적은 원래 설계(2026-11-07 `market-data-codex-design` 접근 2)와 같다: 다른 회사 모델이 반대편에서 반박해 같은 모델끼리의 쏠림을 줄인다. 이를 양쪽 방향으로 넓힌다. 토론 턴이 늘어 세션 시간이 늘어나므로 제한 시간도 함께 조정한다. 묶어서 한 번에 릴리스한다(PR #44·#45 포함).

## 목표 · 비목표 · 반목표
- 목표: Codex 참여가 켜진 새 연구는 **교차 토론 두 판**을 동시에 진행한다 — 1판(A) Claude 강세 ↔ Codex 약세(지금과 같음), 2판(B) Codex 강세 ↔ Claude 약세. 요약에서 두 판의 같은 편을 합쳐 최종 판단에 넘긴다. 종목 분석(spot)·시장 판단(market)·파생 분석(fo) 세 곳 모두.
- 목표: 4인 토론 세션은 제한 시간을 자동으로 늘린다(사용자 설정 파일을 고치지 않아도). Codex 턴이 Claude 세션의 명령 도구 제한(기본 약 120초)에 잘리지 않게 한다.
- 비목표: 한 판에 네 명(같은 편끼리도 서로 보는 구성 — 쟁점 5개 상한·24KB 입력 상한·상대 1명 전제를 전부 새로 짜야 함, 사용자가 A 선택). 단타 판정자(haiku 단발, 토론 없음). 보유 재평가(resume)·스크리너·발굴·복기(토론 없음).
- 반목표: 업데이트 시점에 진행 중이던 연구가 「정책 변경」으로 거부되는 것. Codex 를 못 쓰는데(꺼짐·사용량 조절·호출 상한) 4인 토론을 고집해 분석이 실패하는 것. 같은 모델끼리만 맞붙는 판이 생기는 것.

## 접근 (사용자 선택: A)
- **A(선택) 교차 토론 두 판**: 판마다 상대가 정확히 한 명이라 기존 반박 검사(`turn_error` 상대 쟁점 전부 응답)·쟁점 5개 상한·24KB 입력 상한·`research_summary` 를 판 단위로 그대로 쓴다. 두 판 모두 「다른 모델이 반대편」. 단점: Claude 강세와 Codex 강세는 서로 직접 보지 않는다(요약에서만 합쳐짐).
- B 한 판 4명: 같은 편끼리도 보지만 쟁점 두 배(5개 상한 초과 위험)·입력 크기 상한 근접·반박/요약/증명 재작성 — 제외.

## 현재 구조(조사 결과 요약)
- 오케스트레이터 `claude -p` 세션 하나가 서브에이전트(`researcher-bull/bear`, tools 없음)와 `trader tool checkpoint …` 로 단계를 진행: 애널리스트 → (합의 시 토론 생략, Codex 켜짐이면 금지) → `20_debate_r0_{bull,bear}` → `r1..rounds` → `25_research_summary` → trader → `30_decision`.
- Codex 턴: `trader tool checkpoint codex-turn` → `research_policy.generate_turn` → `codex.run_turn`(bear 고정 PROMPT, `side=="bear"` 강제, 24KB 상한, `research_calls` 증명) → `{turn_prefix}{n}_bear`.
- 정책 `research_policies`(해시 고정): `bull: claude`, `bear: codex|claude`. Codex 켜짐이면 runner 가 `bear_agent` 서브에이전트를 빼고 「Codex 토론 정책」 절을 프롬프트에 붙인다.
- 2측 하드코딩: `schemas.Side`·`issue_id` 패턴, `tooling.DEBATE_RE`, `spot/schemas._STAGE_PATTERNS`, `spot/ledger._DEBATE_RE`, `debate.research_summary`, `tooling.summarize_turns`, `research_policy.payload/write_error/completion_error/turn_error`, `codex.run_turn/PROMPT`, spot·fo·regime tools·fake, `evaluation/metrics`, 원장 `debate_turns UNIQUE(decision_date,market,ticker,round,side)`, 웹 `Decision.tsx`·`DebateComparison.tsx`·`trace.ts`.
- 시간: `llm.timeout_s` 900(spot analyze·fo), `timeout_by_kind.market` 600. 사용자 설정 파일이 `timeout_s`·`timeout_by_kind` 를 명시하고 있어 **기본값 변경만으로는 이 맥에 적용되지 않는다.** Codex 턴은 `codex_debate.timeout_s` 180 이 각 호출에 걸리지만 Claude 세션 시간 안에서 돈다. 러너 env 에 `BASH_DEFAULT_TIMEOUT_MS`·`BASH_MAX_TIMEOUT_MS` 가 없다(Claude Code Bash 기본 제한 약 2분 — 180초 Codex 호출과 충돌 가능, 아직 Codex 실호출 0건이라 미관측).
- 실측(최근 7일): 분석 세션 91건, 평균 5분 18초, 최장 10분 40초.

## 설계

### 1. 정책: 판(lane)
- `research_policy.current()` 정책 **version 2**: Codex 참여 켜짐이면 `lanes = [{"lane":"a","bull":"claude","bear":"codex"}, {"lane":"b","bull":"codex","bear":"claude"}]`, `rounds = max(1, debate_rounds)`. 꺼짐·조절로 건너뜀이면 지금과 같은 1판 `[{"lane":"a","bull":"claude","bear":"claude"}]`(`codex_skipped` 유지). 기존 키 `bull`/`bear` 는 1판 값으로 남겨 하위 호환.
- `codex_allowance(need=...)`: 필요한 Codex 호출 = 판 B 강세 + 판 A 약세 = `2 × (rounds+1)`.
- **업데이트 보호**: 저장된 version 1 정책(진행 중 연구 — Codex 켜짐·꺼짐 모두)은 해시 비교 없이 그대로 쓴다(2인 토론으로 끝까지, Codex 약세 v1 PROMPT 해시 유지). version 2 정책만 새 구조로 검사.
- **세션 단위 모드**: 러너는 프롬프트·서브에이전트 목록을 세션 하나에 하나만 만든다. 세션의 연구 중 하나라도 저장된 v1(Codex 켜짐) 정책이면 그 세션은 옛 2인 모드로 돌고, 그 세션에서 새로 시작하는 연구도 v1 2인으로 시작한다(업데이트 직후 하루만 생기는 경우). 그 밖에는 v2.

### 2. 단계 이름
- 판 A: 기존 그대로 `{turn_prefix}{n}_{side}` (spot `20_debate_r0_bull`). 판 B: `ResearchSpec` 에 `cross_turn_prefix` 추가 — spot `20_debatex_r`, market `_market_debatex_r`, fo `_fo_debatex_r` (예: `20_debatex_r1_bear`). 정규식(`tooling.DEBATE_RE`, `spot/schemas._STAGE_PATTERNS`, `spot/ledger._DEBATE_RE`, fo·regime tools)은 두 접두사를 모두 인정하고 판을 함께 돌려준다.
- `Side`·`issue_id` 패턴은 그대로(bull|bear). 판 B 쟁점 ID 충돌을 막기 위해 판 B 턴의 issue_id 는 같은 `side:` 접두를 쓰되 판 단위로만 고유하면 된다(검사는 판 단위).

### 3. 턴 생성
- Claude 턴: 판 A 강세 = `researcher-bull`, 판 B 약세 = `researcher-bear`. 두 서브에이전트를 모두 남긴다(Codex 켜짐이면 지금은 bear 를 빼지만 4인에서는 판 B 약세를 맡는다). `checkpoint bundle --kind researcher --round n --side s --lane a|b` — 입력의 `opponent_previous` 는 **같은 판** 상대 직전 턴. `checkpoint write --stage …` 는 판 B 접두사도 받는다.
- Codex 턴: `checkpoint codex-turn --round n --side bull|bear`(판은 공급자 배정에서 자동: Codex 강세=판 B, Codex 약세=판 A). `codex.run_turn` 은 `side` 를 받아 측별 PROMPT(약세 기존 문구 유지 — 해시 보존, 강세 새 문구)·출력 `side` 일치 검사·`call.agent` 를 측에 맞게. fo·market 의 codex-turn CLI 도 같은 인자.
- 오케스트레이터 런타임 절(`runner.py` 「Codex 토론 정책」)을 판 두 개 기준으로: 라운드마다 4턴(판 A 강세 Claude·약세 Codex, 판 B 강세 Codex·약세 Claude), 같은 라운드 4턴은 직전 라운드만 보므로 동시에 진행 가능. Claude 서브에이전트 2개는 병렬, Codex 2턴은 Bash 순차(각 최대 `codex_debate.timeout_s`).

### 4. 검증·증명
- `write_error`: 판별로 직전 라운드 양측 존재. 공급자 배정과 다른 기록 금지(판 A 약세·판 B 강세에 Claude 기록 금지, 판 A 강세·판 B 약세에 Codex 기록 금지).
- `turn_error`: 판 단위(상대 = 같은 판 반대측).
- `completion_error`: 판별 0..rounds × 양측 존재, Claude 측은 `provider=="claude"`, Codex 측은 `research_calls` 해시 증명(측별 prompt_hash).

### 5. 요약·최종 판단
- 판별 `research_summary`(기존 함수) → `merge_lanes()`. 1판이면 지금과 똑같은 출력.
- **합친 턴의 형태는 `DebateTurn` 그대로**(소비자 수정 최소화): 같은 편 두 턴을 부호 붙인 확신(bullish +c, bearish −c, neutral 0)으로 평균 → 대표 `stance`(부호 확신 **평균** > 0.1 bullish, < −0.1 bearish, 그 밖 neutral)·`conviction`(|평균|), `arguments`·`rebuttals` 는 두 판에서 번갈아 골라 5개 상한. 판별 원본은 요약의 별도 키 `by_lane: {a: {...}, b: {...}}`(턴 밖)에 둔다. `round0` 도 같은 방식.
- **경고 신호(사용자 결정: 한 판이라도 켜지면 적용)**: `sycophantic`·`convergence_review` 는 판별 값의 OR, `preferred` 는 어느 판이든 `round0` 이면 `round0`. `convergence`·`gap_round0/gap_final`·`timing_agree` 는 합친 턴으로 다시 계산(정의 불변).
- `trader.md`·시장 전략가·fo 판단 프롬프트: 「강세·약세 각각 Claude·Codex 두 의견을 합친 것, 의견이 갈리면 by_lane 참고」 한 단락. `tooling.trader_summary` 의 24KB 축약은 합친 결과에 적용.
- `evaluation/metrics` 수렴률은 **판 단위**로 센다(각 판이 실제 강세·약세 한 쌍의 토론이므로) — 4인 토론 결정은 표본 2개. 원장 `lane` 열로 묶는다. 리포트에 표본 정의를 한 줄 적는다.

### 5-1. 도중 Codex 실패(사용자 결정: 다시 띄울 때 Claude 로)
- 4인 연구에서 Codex 턴이 실패하면(오류·한도·상한 경합) 그 세션은 지금처럼 실패로 끝난다. `generate_turn` 이 실패를 정책 행에 표시(`codex_failed_at`, 사유).
- 같은 연구를 다시 시작하면 `start()` 가 표시를 보고: 토론 단계 체크포인트(두 판 모두)를 연구 폴더 안 `_codex_failed_<시각>/` 로 옮겨 보존하고, 애널리스트 리포트는 그대로 둔 채, 정책 행을 **Claude 2인(1판, `codex_skipped: "Codex 도중 실패 — …"`)** 으로 교체해 토론을 처음부터 다시 한다. 교체는 이 경우에만 허용(정책 변경 거부의 유일한 예외), 원장 `research_policies` 에 이전 해시·교체 사유를 남긴다.
- **같은 잡에서 자동 재실행(2026-11-10 사용자 결정 A)**: 세션이 실패했고 그 세션 연구에 도중 실패 표시가 남아 있으면 커널 `run_session` 이 같은 잡 안에서 한 번 다시 띄운다(재시작의 `start()` 가 위 교체를 한다). 재실행은 한 번뿐이고, 상한·감속으로 거부되면 첫 실패 결과를 돌려준다. 다시 띄우지 않으면 그 종목은 그날 판단이 빠졌다(재실행 잡이 없었다). 추가 비용은 Claude 세션 1회.
- **재실행 범위 확장(2026-11-10 사용자 결정)**: Codex 표시가 없어도 세션 실패 사유가 「필수 토론 미완료」(`research_policy.INCOMPLETE` — Claude 턴 누락 등)면 같은 잡에서 한 번 다시 띄운다. 체크포인트가 남아 빠진 턴부터 이어간다. 타임아웃 등 토론과 무관한 실패는 다시 띄우지 않는다.

### 6. 원장·웹
- `debate_turns` 에 `lane` 열 추가 + 고유 키 `(decision_date,market,ticker,lane,round,side)` — 새 마이그레이션(테이블 재생성·복사, 기존 행 lane='a'). 원장 적재는 두 접두사.
- 웹 결정 상세: 토론 표에 「1판/2판」·공급자 열(「강세 · Claude」 「약세 · Codex」), `sideLabel` 은 bull/bear 그대로. `DebateComparison` 확신 행 키에 판 추가. 시스템 › Codex 토론 설정 카드의 역할 표시를 「1판 Claude 강세 · Codex 약세 / 2판 Codex 강세 · Claude 약세」.

### 7. 제한 시간
- 새 설정 `llm.cross_debate_extra_s: int | None = None`(0~3600). None(기본)이면 4인 연구마다 `(rounds+1) × (codex_debate.timeout_s + 120)` 초를 더한다(늘어난 Codex 턴 + Claude 턴 여유; rounds 1·Codex 180초면 +600 → spot·fo 1500, market 1200 / rounds 2면 +900). 정수면 연구마다 그 값. 세션에 4인 연구가 여럿이면 합산. 2인·Codex 꺼짐 연구는 더하지 않는다. 사용자 설정 파일에 키가 없으므로 기본 계산이 바로 적용된다.
- 러너 env 에 `BASH_DEFAULT_TIMEOUT_MS`·`BASH_MAX_TIMEOUT_MS` = `(codex_debate.timeout_s + 60) × 1000` 이상(기본 240000) — Codex 턴이 명령 도구 제한에 잘리지 않게. 세션 시간 안에서만 도는 것은 그대로.
- 사용량 화면·결정 상세에 실제 소요가 이미 보이므로 첫 며칠 뒤 `cross_debate_extra_s` 를 다시 정한다(문서에 기록).

## 결정한 것
- 사용자: 접근 A(교차 두 판), 4인 토론·시간 조정을 마친 뒤 PR #44·#45 와 함께 한 번에 릴리스.
- 제안(A 선택 때 함께 제시, 이의 없음): 세 곳(spot·market·fo) 적용, Codex 를 못 쓰면 2인 Claude 토론, 4인 세션만 제한 시간 자동 연장(+600초), 결정 상세에 판·공급자 표시.

## 빌더에게 맡김
- 판 B 접두사 정확한 문자열(위 제안), 합친 요약의 우선순위 규칙 세부, 강세 Codex PROMPT 문구(약세 문구와 대칭), 웹 표 레이아웃, 페이크(FAKE_CLAUDE) 경로가 4턴을 만들게 하는 방식.
