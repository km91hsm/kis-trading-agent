# M11 웹 UI 설계 — 화면·채널·자원 API

작성 2026-11-04. 상위 스펙 `2026-11-02-kis-trading-agent-replatform-design.md` §2.5(12 자원)·§3 M11/M11b 의 "③ 웹 UI 재설계 브레인스토밍" 결과다. 이 문서가 M11(백엔드 자원 API)과 M11b(새 프런트) 두 플랜의 공통 근거다.

## 1. 의도

1인 운영자가 **휴대폰과 PC 에서** 자동매매 데몬의 상태를 보고, 드물게 개입(승인·일시정지·모드 전환·잡 수동 실행)하는 도구. 옛 웹은 "기능마다 한 페이지" 였고 대시보드 한 화면이 17 호출을 했다. 새 웹은 "무엇을 알고 싶어서 열었나" 기준 다섯 묶음이고, 각 화면이 소수의 자원 API 로 완결된다. 옛 화면은 어느 것도 모양을 승계하지 않는다(사용자 결정).

### 채널 분담(사용자와 합의)

| 채널 | 역할 | 내용 |
|---|---|---|
| 텔레그램 | 찾아오는 것(push) | 아침 리뷰·마감 리뷰, 체결·손절·킬스위치 등 사건 알림, **승인 요청(인라인 버튼 승인/거절)**, **운영 이상 알림**(데몬·잡·토큰·시세·상한). 읽기 + 버튼 하나. 끝에 웹 해당 화면 링크 1줄 |
| 웹 | 찾아가는 것(pull) | 아래 다섯 묶음. 승인은 웹에서도 같은 자원을 건드려 양쪽이 즉시 일치 |

## 2. 목표 · 비목표 · 반목표

**목표**
- 휴대폰 세로 한 열에서 「오늘」「계좌·보유」가 완결된다(1급 화면). 나머지 셋은 큰 화면 위주, 휴대폰에서도 읽힌다.
- 모든 화면 상단에 **상태 띠**(정상/주의/장애 + "N초 전 갱신") 와 **초 단위 시계**(KST, 장 세션 표시)가 있다.
- 승인 메시지는 종목·방향·수량·금액 + **판정 근거 요약**(3~4줄) + 전체 근거 링크.
- 보유 종목에 **손절가·목표가를 대략적으로** 표시한다(출처 라벨 포함).
- 통합 계좌 현황은 **원화 합계와 달러 합계를 나란히** 두고, 참고 줄에 적용 환율과 환산 합계를 표기한다(합계 계산에는 쓰지 않음).
- Tailscale(사설 VPN) 경유 휴대폰 접근을 전제로 인증 3등급을 유지한다.

**비목표**
- 매매 로직·리스크 수치·에이전트 프롬프트의 의미 변경(상위 스펙 비목표 그대로).
- 웹 푸시 알림(PWA push). 알림은 텔레그램이 맡는다.
- 공인 인터넷 노출, 다중 사용자, 로그인 화면.
- 브라우저 E2E 를 CI 에 넣는 것(Chromium 없음). 로컬 수동 점검.

**반목표(이렇게 되면 실패)**
- 휴대폰에서 가로 스크롤 표를 봐야 한다.
- 실전 계좌 쓰기 동작을 한 번 눌러 실행할 수 있다.
- 화면이 오래된 데이터를 최신처럼 보인다(SSE 끊김을 숨김).
- 웹과 텔레그램이 같은 승인에 대해 다른 상태를 보인다.
- 프런트가 SSE 키나 메뉴 항목을 손으로 적어 백엔드와 어긋난다.

## 3. 제약

- 상위 스펙 §2.5 의 12 자원과 옛 엔드포인트 대응표는 **그대로 구현**한다(M11 완료 기준 "대응표 100%"). 이 문서는 자원의 **응답 모양**과 몇 개 하위 경로를 더 정한다(§7).
- 인증 3등급 유지: 조회 토큰 없음(루프백 + Host 허용 목록), 쓰기 `Bearer WEB_TOKEN`, 키 입력·토큰 받기는 `is_direct_local`(이 PC 에서 직접 연 요청)만. **Tailscale**: 옛 구조를 승계한다 — uvicorn 은 루프백에만 열고 `tailscale serve` 가 tailnet 이름으로 넘긴 요청만 받으며, `settings.web.tailscale_hosts`(Host 허용 목록)와 `settings.web.tailscale_users`(`Tailscale-User-Login` 허용 로그인; 비어 있으면 tailnet 구성원 전부) 로 두 번 거른다. 사용자가 serve 없이 tailnet IP 로 직접 붙는 경우는 `tailscale_users` 검사가 불가능하므로 설정 문서에 "serve 사용 권장" 을 적고, 그 경우 조회도 열리는 것을 명시한다. tailnet 밖은 열지 않는다.
- **웹 프로세스는 데몬과 분리**(`trader web`, 기존 CLI 그대로). 웹은 원장을 읽고 쓰기만 하며 브로커·알림 객체를 쥐지 않는다. 쓰기 동작은 원장 상태 변경 + 데몬 큐 요청으로 전달되고 **집행은 데몬**이 한다(§8.10). 리허설의 포트 분리(8788)와 장애 격리를 위해서다.
- import 3층·레지스트리 계약(§2.3) 유지: 라우터·SSE 키·nav 는 각 기능 `feature.py` 의 `routes`/`sse_keys`/`nav` 로만 등록, 커널 `web/app.py` 가 마운트. 커널은 기능 이름을 모른다.
- 생성 파일: `web/src/generated/sse-keys.ts`(기존) + **`web/src/generated/nav.ts`**(신설, `Feature.nav` 에서) — 둘 다 `trader dev gen-sse-keys` 류 명령 + CI 동기 테스트.
- 프런트 스택: React 19 + Vite + react-router + `lightweight-charts` + Pretendard, 손으로 쓴 CSS 변수. 새 의존성은 이 넷 외 추가 금지(의존성 추가는 Windows 호환 확인 후 플랜에 명시).
- 공개 저장소: 테스트 픽스처는 합성값, 템플릿은 자리표시자.
- Windows CI 동일 통과, 모든 파일 I/O `encoding="utf-8"`.
- 옛 `tests/web/*` API 테스트는 자원 경로로 바꿔 이식(동작 보존 확인용).

## 4. 접근 방식

| | 방식 | 평가 |
|---|---|---|
| **1 (선택)** | 옛 스택 그대로, 화면은 전부 새로. 모바일 우선 + PWA 매니페스트(홈 아이콘·전체화면, 푸시 없음) | 새 `web/` 셸·스펙(생성 `.ts`, vitest, CI typecheck/build)에 그대로 맞고 의존성 증가 없음. 비용은 화면 전부 새로 그리기(어느 방식이든 같음) |
| 2 | 1 + 컴포넌트 라이브러리(Tailwind + shadcn 류) | 모바일·접근성 기본값을 빨리 얻지만 의존성·빌드 설정 증가, 1인 운영 도구가 흔한 SaaS 모양이 되기 쉬움. 디자인 통제를 우리가 쥐는 쪽을 택해 제외 |
| 3 | 서버 템플릿(HTMX 류) | 빌드 없음이 장점이나 스펙의 SSE 키 생성·vitest 이식·차트 상호작용과 충돌. 제외 |

사용자가 1 을 택했다(2026-11-04).

## 5. 공통 요소

- **상태 띠**(모든 화면 최상단 1줄): 데몬 생존·잡 이상·토큰·시세·세션 상한을 합산한 3단계(정상/주의/장애) 색 점 + 짧은 사유 + "N초 전 갱신". 출처는 `system/health`(M12 `kernel/ops` 가 판정을 채우기 전까지는 하트비트·runs 오류·`llm_rate_limits` 로 M11 이 계산하는 임시 판정; M12 가 `kernel/ops` 를 넣으면 그 결과로 교체 — 자원 모양은 바뀌지 않게 §7 에 고정).
- **시계**: KST 시:분:초, 옆에 현재 장 세션(KR 정규/장외, US 정규 KST + ET 병기, 파생 창). 클라이언트 1초 틱, 서버 `now` 는 SSE ping 과 모든 응답 헤더 `X-Server-Time` 으로 받아 오프셋 보정(±1초 이상 틀어지면 띠에 "시계 어긋남" 표시).
- **실시간 갱신**: 화면에 보이는 숫자는 전부 살아 있어야 한다(사용자 요구: 현재가·계좌 현황·시계 모두). 아래 §5.1 의 실시간 모델을 따른다. 휴대폰 백그라운드 복귀 시 SSE 자동 재연결, 재연결 전에는 30초 폴링. 보이지 않는 탭은 시세 폴링을 멈춘다(배터리·KIS 한도).

### 5.1 실시간 모델

| 무엇 | 출처·주기 | 화면 지연 목표 | 비고 |
|---|---|---|---|
| KR 현물 현재가·지수 | `quotes` 허브(옛 `live_quotes` 이식): KIS 웹소켓 체결가(`H0STCNT0`)·지수(`H0UPCNT0`) 를 **보이는 종목만 임차**(40칸, 30초 임차). 브라우저는 보이는 탭에서 1초 폴링 | ≤1초 | 실전 체결통보 리스너가 앱키 세션을 쓰는 동안은 REST 로 돈다(옛 `blocked()` 승계), 띠에 "시세 REST" 표시 |
| 호가(차트 화면) | 같은 허브, `H0STASP0`, 10초 임차 | ≤1초 | 차트 화면이 보는 동안만 |
| US 현물 현재가 | 허브가 KIS 해외 현재가 REST 를 **5초 간격**으로 폴링(미국 정규장 중만, 보이는 종목만) | ≤5초 | 앱키당 초당 1건 한도를 데몬과 공유하므로 더 빠르게 하지 않는다. 장외는 60초 |
| 파생 현재가(선물·옵션) | 허브가 `kernel/broker/market_fo` REST 를 **2초 간격**으로 폴링(파생 창 중, 보유·관심 종목만). 옵션 이론가는 클라이언트가 `fo.pricing.reprice` 와 같은 식으로 지수 틱에 맞춰 보정 표시 | ≤2초 | 평가는 포인트·원화 나란히 |
| 환율 USD/KRW | `quotes/fx` 하루 1회(전일 종가) | — | 참고 표기 전용 |
| 보유 평가손익·계좌 평가액 | **클라이언트 재계산**: 보유 수량 × 현재가(위 시세) − 평단, 1초. 현금·예수금·NAV 기준값은 서버 `account_nav`/잔고 마지막값 | ≤1초(평가) / 체결 즉시(현금) | 현금은 KIS 잔고 API 를 매초 부를 수 없어 **체결·주문 사건 뒤 + 60초 주기** 로 서버가 갱신(`balance` 잡 승계). 화면은 "현금 N초 전" 을 따로 표기 |
| 결정·주문·체결·승인·알림·잡·runs | 원장 변화를 SSE `/api/events` 키 변화로 전달, 화면이 해당 자원만 재조회 | ≤2초 | SSE 스냅샷 주기 1~2초(현 `poll_s`), noisy 키(데몬 하트비트) 는 전역 갱신에서 제외 |
| 데몬 생존 | 하트비트 파일 분 단위 지문 + health | ≤60초 | 상태 띠 |
| 시계 | 클라이언트 1초 틱 + 서버 시각 보정 | 0 | §5 시계 |

원칙: 실시간 값은 **표시 전용**이다. 신호·주문·게이트는 이 값을 읽지 않는다(옛 불변식 승계). 서버는 각 시세 응답에 `next_poll_ms` 를 넣어 장 세션에 따라 브라우저 폴링 간격을 지시한다(정규장 1000, 장외 60000 등) — 브라우저가 주기를 하드코딩하지 않는다.
- **날짜 선택**: 「오늘」에 날짜 선택이 있어 과거 날짜의 결정·주문·알림을 같은 화면으로 본다. 기본 오늘(KST 거래일).
- **금액·시간 표기**: 원화 정수 천 단위 구분, 달러 소수 둘째 자리, 파생은 포인트와 원화 평가 나란히. 시각은 전부 KST, 미국 장 관련은 괄호 ET 병기.
- **실전 구분**: 실전 계좌 카드·버튼은 별도 색·"실전" 라벨. **실전 쓰기 동작**은 토큰 + 확인 대화상자에 **계좌 이름 재입력**이고, **서버도 강제**한다(요청 본문 `confirm` 에 계좌 id 가 없으면 400; 옛 `confirm="LIVE"`·텔레그램 `실전` 확인어 승계). 실전 쓰기 동작의 정의: 실전 계좌의 모드 변경, 실전 계좌 승인(approve), 실전 `auto` 계좌가 하나라도 있을 때의 전역 **재개**(일시정지는 확인 없음 — 안전한 방향), 대상에 실전 계좌가 포함되는 `trading` 큐 kind 의 수동 실행. 모의는 확인 대화상자만(서버 강제 없음). 텔레그램 버튼은 실전이면 **2단계**(누르면 "실전 <계좌> 승인 — 확인" 버튼이 다시 뜸), 모의는 1단계.
- **다크 모드**: OS 설정 따름 + 토글(localStorage).
- **용어 설명**: 별도 페이지 없음. 화면 안 툴팁(옛 `glossary` 순수 함수 이식).
- **쓰기 토큰**: 「시스템」에서 `WEB_TOKEN` 을 한 번 입력하면 브라우저 localStorage 에 저장, 모든 쓰기 요청 `Authorization: Bearer`. 토큰 받기(`/system/bootstrap-token`) 는 직접 로컬만(기존).
- **조작 이력**: 모드·일시정지·승인·잡 수동 실행·설정 변경은 **누가(채널: web/telegram/cli)·언제·무엇을·전후값** 을 원장에 남기고 「시스템」에서 본다(§8 `control_log`).

## 6. 화면(다섯 묶음)

라우트 다섯 개. 휴대폰은 하단 탭 다섯 개, PC 는 왼쪽 세로 메뉴. 메뉴 항목은 `nav.ts` 생성 파일에서 온다(기능이 `Feature.nav` 로 등록; 기능이 꺼져 있으면 항목도 사라짐).

### 6.1 오늘 `/` (1급·첫 화면)
위에서 아래로:
1. 대기 승인(있을 때만, 최상단 강조): 계좌·종목·방향·수량·금액·근거 3~4줄·만료 시각·[승인][거절]. 결정 상세 링크.
2. 레인 카드 3장(현물·단타·파생): 오늘 결정 수(매수/매도/보류), 주문·체결 수, 미체결, 레인 상태(세션 완료/대기/정지, 다음 예정 시각), 세션 상한 잔여("오늘 fo 2/3", 현물 monitor 등). 없는 레인(계좌 없음)은 카드 생략.
3. 오늘 결정 목록(시간 역순): 레인·계좌·종목·판정·수량·상태(대기/승인/주문/체결/거절/만료) → 결정 상세.
4. 오늘 주문·체결 타임라인(시:분:초).
5. 알림 피드: 텔레그램으로 보낸 최근 50건(같은 본문), 종류 필터.
6. 데몬 다음 할 일: 다음 예정 잡 3개(시각·이름).

**결정 상세 `/decisions/{asset}/{id}`**(`asset` = spot|fo; spot 은 하루 1행 결정이고 `decision_rounds` 를 상세 안에 라운드 목록으로 펼친다; 단타는 spot 결정 표를 쓰므로 `spot`): 근거 전문, 에이전트 보고·토론 턴(`analyst_reports`·`debate_turns`), 체크포인트 목록, 리스크 체크 결과, 이어진 주문·체결·결과(`outcomes`)·복기 메모리 연결. 텔레그램 "전체 근거 보기" 가 여기로 온다. 파생 결정(`fo_decisions`)도 같은 화면 모양(필드 일부 다름).

### 6.2 계좌·보유 `/accounts` (1급)
- **통합**(기본): 맨 위 합계 두 숫자(원화 계좌 합계 / 달러 계좌 합계: NAV·당일 손익), 참고 줄 "적용 환율 USD/KRW ○○○(○월 ○일 종가) → 환산 합계 ○○○". 아래 계좌 카드(이름·레인·모의/실전·모드·일시정지 여부·NAV·당일 손익·보유 수·대기 승인 수). 전역 일시정지 토글은 여기 상단.
- **계좌별 `/accounts/{id}`**: NAV 추이 차트(`account_nav` 또는 `fo_nav_snapshot`), 보유 목록(종목·수량·평단·현재가·평가손익·**손절가·목표가(출처 라벨)**; 현물은 lot 펼침, 파생은 포지션 상태·다리·만기), 오늘 주문·체결, 리스크 한도 대비 사용량(`positions/risk`), 모드 전환(auto/approve/off; 실전 auto 는 LIVE 확인 필요 안내)·계좌 일시정지.
- 손절·목표 출처: 현물 = spot `monitor_rules.effective_stop`(결정 `stop_loss` / 보유 최고 바닥 / 기본 비율) 과 결정 `take_profit`; 단타 = scalp exits 규칙의 손절·목표; 파생 = fo triggers 의 손절·만기 청산 조건. 모두 "대략" 이며 각 값에 `source` 를 붙여 화면이 라벨로 보인다(예: "결정", "추적", "기본 −7%").

### 6.3 성과 `/performance`
누적·기간 통계(`analytics/performance`, 레인·계좌·시장 필터), 벤치마크 대비, 레인·계좌 비교(옛 Compare 흡수), 판정 분포·수렴률·보정(`calibration_runs`), 섀도 포트폴리오·RL 비교, 복기 메모리 목록(검색). 큰 화면 위주, 휴대폰은 카드 한 열.

### 6.4 데이터 `/data`
- **뉴스**(옛 Data 뉴스 절 승계·보강): 시장 필터(KR/US), 최근 48시간·100건, 행마다 수신/발행 시각·소스(`source · query`)·제목(링크)·**라벨**: 종목 칩(유니버스 종목명 매칭 `tickers` → 종목명 표시, 클릭하면 차트), 촉매 여부(`is_catalyst` 소스), 테마 칩(그 기사가 속한 테마), "판정에 쓰임" 표시(결정 trace 가 이 기사를 참조했으면). 수동 입력(읽은 기사 붙여 넣기 → `POST /news/manual`)과 「지금 수집」(`news_collect` 잡) 버튼.
- **이벤트**(공시·거시·KSD 일정): 날짜·라벨·관련 종목 역할 줄·대표 뉴스 1건(옛 `/system/events` 승계).
- **테마 열기**: 오늘 상위 테마·점수·구성 종목·히트 추이(`theme_heat`).
- **지표·모델**: features 스냅샷 최신/이력, 모델 목록·승격 상태(옛 Ml 흡수), 「오늘 스냅샷」「파생 열 재계산」「3년 백필」 잡 버튼.
- **유니버스·감시목록**(편집은 쓰기 토큰), **차트**(종목 검색 → 일봉/분봉 + 체결 마커·손절·목표선, 옛 Chart 흡수).

### 6.5 시스템 `/system`
- **Claude 사용량**(옛 Dashboard 칩 + System `UsageBlock` 승계·보강): 5시간 창·7일 창·지출 한도 **미터**(사용률 %, 관측 시각 "N분 전", 리셋까지 남은 시간, 관측 출처 session/probe/statusline), 감속 상태(ok/soft/hard, 사유, 켜기/끄기 토글은 쓰기 토큰), 이 저장소 세션 집계(5시간·7일: 세션 수·비용 USD·오류 수, 오늘 세션 수/상한), 종류별 오늘 상한(현물 analyze·resume·fo 등), 「지금 확인」(`usage_probe` 잡). 화면 접속·새로고침 때 `POST /system/usage/ensure-fresh` 로 5분 넘은 관측만 갱신(옛 동작). **같은 사용률 칩을 「오늘」 상단 띠와 PC 메뉴 하단에도 작게 둔다**(70% 미만 초록·90% 미만 노랑·그 밖 빨강).
- health 상세(상태 띠의 사유 목록), 데몬(하트비트·버전·잡 표·다음 실행), 잡 대기열(요청·진행·취소, 수동 실행 버튼은 kind 레지스트리에서), 실행 기록 `runs`·LLM 호출 `calls`(에이전트·모델·토큰·비용·소요), 설정값 조회·일부 변경(감시목록·스로틀), 시크릿 상태·키 입력(직접 로컬만, 휴대폰에선 "이 PC 에서만" 안내), 쓰기 토큰 저장, 조작 이력, 저장소 상태(saveticker 재시작 등).

## 7. 자원 API — 화면이 요구하는 모양

12 자원과 옛 대응표는 상위 스펙 §2.5. 여기서 더 정하는 것:

| 자원 | 소유 기능(라우터 등록) | 추가·확정 사항 |
|---|---|---|
| `accounts` | accounts | `GET /accounts` 행에 `lane`(spot/scalp/fo), `env`, `mode`(effective + source), `paused`, `nav`, `day_pnl`, `currency`, `positions_count`, `pending_approvals`. `GET /accounts/overview` 가 통합 합계(원화·달러 분리) + `fx: {pair, rate, as_of, converted_total}` |
| `accounts/{id}/positions` | spot·scalp·fo 가 각자 자기 계좌의 행을 내는 라우터를 등록, accounts 가 묶지 않음(레인은 import 금지) → **커널 `kernel/web/merge.py` 가 같은 경로의 기능 응답을 합친다**(§8). 각 행 `stop: {level, source}`, `target: {level, source}` (없으면 null) |
| `decisions` | spot(현물·단타 공유 `decisions` 표), fo(`fo_decisions`) — 둘을 같은 경로에서 `asset` 로 구분해 커널이 합침. `GET /decisions?date=` 날짜 선택, `/{id}/trace` 에 `analyst_reports`·`debate_turns`·`risk_checks`·체크포인트 목록 |
| `orders` | spot(+scalp origin)·fo(`fo_order_legs` → `legs`) 합침, `?date=` |
| `approvals` | **spot**(승인 표는 커널 원장이지만 집행 `executor.approve` 가 spot 소유; fo 는 승인 없음) | `GET` 행에 `account_id`·`env`·`decision: {asset, id}`·`summary`(근거 3~4줄, 결정에서 추출)·`expires_at`; `PATCH /approvals/{id} {action: approve\|reject, confirm?}` 는 원장에 `decided_by/channel` 을 적고 데몬 큐에 spot 의 `approve_execute` kind(fast lane) 를 넣는다(§8.10). 텔레그램 버튼·텍스트 명령도 같은 spot 함수(`approvals.decide`)로 들어온다 |
| `jobs` | **커널**(`kernel/web/routers/jobs.py`: kind 레지스트리·큐·runs·calls 는 커널 소유) | `GET /jobs`(등록 잡 표 + 다음 실행), `POST /jobs {kind, params}`, `DELETE /jobs/{req_id}`, `GET /jobs/requests`, `/runs`, `/calls`; 실행 소스 `web` |
| `events` | 커널(기존) | ping 에 `server_time` 포함. 기능 SSE 키 신설: `positions`·`decisions`·`orders`·`approvals`·`notices`·`fo`·`scalp`·`news`·`theme`·`jobs`·`runs`·`usage`·`control`(옛 지문 목록 승계, 각 소유 기능이 등록) |
| `news` | ingest(+scan theme) | 기존 대응표 |
| `quotes` | **features/quotes 신설**(옛 `live_quotes`·`names`·`charts`·`features` 라우터) | `GET /quotes?market=KR\|US&tickers=`·`/quotes/indices`·`/quotes/fo?codes=`·`/quotes/book?ticker=` 는 §5.1 주기, 응답에 `next_poll_ms`·`source`(ws\|rest)·`at`; `GET /quotes/fx?pair=USDKRW` → `{rate, as_of, source}`(KIS 환율 조회 우선, 실패 시 ingest macro 최근값, 둘 다 없으면 404 → 화면 "환율 없음") |
| `settings` | accounts(paused·secrets), scan(watchlist), 커널(usage-throttle) | 기존 대응표 |
| `system` | 커널(health·status·daemon·usage·bootstrap-token) + 기능별(regime·saveticker·scalp·fo) | **`GET /system/health`** 응답 고정: `{level: ok|warn|fail, reasons: [{area, level, text}], checked_at}`; **`GET /system/notices?limit=50&kind=`**(알림 피드, §8); **`GET /system/control-log?limit=`**(조작 이력) |
| `analytics` | evaluation(performance·calibration·memory·rl), ml, fo(scorecard·report·summary) | 기존 대응표 |

공통: 조회는 토큰 없음, 쓰기(`POST/PATCH/DELETE`)는 `require_token` + `require_same_origin`, 키·토큰 받기는 `require_direct_local`. 응답 시각은 ISO-8601 KST 오프셋 포함. `?format=csv` 는 옛 지원 자원만 유지. 모든 응답에 `X-Server-Time`.

## 8. 커널·기능 변경(새로 필요한 것)

1. **`features/quotes`**(신설, service): 옛 `live_quotes` 허브(임차·단일 스레드·앱키 세션 양보)·`names`·`charts`·`features` 라우터·환율. SSE 키 없음(폴링 자원).
2. **같은 경로 합치기 `kernel/web/merge.py`**: 여러 기능이 같은 자원 경로(`accounts/{id}/positions`, `decisions`, `orders`)에 "부분 응답" 라우터를 등록할 수 있게, 커널이 **`Feature.routes` 대신 `Feature.resources: tuple[ResourcePart, ...]`**(`path`, `fn(deps, params) -> list[dict]`) 를 모아 하나의 경로로 노출하고 결과 리스트를 이어 붙인다(정렬 키 `at` 역순). 단일 소유 자원은 기존 `routes` 그대로.
3. **알림 피드 `notices`**(원장 마이그레이션 15, 커널): 커널 `notify` 가 보내는 모든 메시지를 `notices(id, at, kind, account_id, text, channel, ok)` 에 기록(outbox 와 별개, 전송 실패도 기록). `kind` 는 메시지 종류(`approval`, `fill`, `stop`, `review_am`, `review_pm`, `ops`, …) — 기존 `messages.py` 호출부가 kind 를 넘기도록 Notifier.send 에 `kind: str = "info"` 인자 추가(기본값으로 기존 호출 보존).
4. **조작 이력 `control_log`**(같은 마이그레이션 15, 커널): `control.set_flag`·`modes.set_account_mode`·`set_paused`·승인 처리·잡 수동 요청이 `(at, channel, actor, key, old, new)` 를 남긴다. `channel` 은 호출자가 넘긴다(web/telegram/cli/daemon).
5. **텔레그램 인라인 버튼**(커널 notify): `TelegramNotifier.send(..., buttons: list[tuple[label, data]] | None)` 로 `reply_markup` 전송, `TelegramReceiver.poll` 이 `callback_query` 를 `Update(kind="callback", data=..., message_id=...)` 로 돌려준다. 커널은 기능 이름을 모르므로 **`Feature.telegram_callbacks: tuple[TelegramCallback, ...]`**(`prefix`, `handler(ctx, data) -> str`) 를 신설하고 `commands.poll_once` 가 `data` 접두(`apv:`)로 기능 핸들러에 넘긴 뒤 `answerCallbackQuery` + 원문 메시지 편집("처리됨 ✓ 텔레그램/웹"). 실전은 2단계 버튼(§5). 폴백은 기존 `/approve <id>`·`/reject <id>` 텍스트 명령 유지(spot 등록). 승인 메시지 본문: 계좌·종목·방향·수량·금액·근거 3~4줄·만료·웹 링크(`{web_base}/decisions/{asset}/{id}`, `web_base` 는 설정 `web.public_base`, 기본 tailnet 호스트명). 버튼 핸들러는 데몬 안에서 돌므로 spot `approvals.decide` 가 집행까지 바로 한다(§8.10).
6. **운영 이상 알림**: 판정은 M12 `kernel/ops` 몫. M11 은 `system/health` 모양을 고정하고 하트비트·runs 연속 실패·토큰·`llm_rate_limits` 임박으로 임시 판정을 채운다. 텔레그램 발송(쿨다운 포함)은 M12 에서 같은 판정 결과를 소비한다.
7. **`nav.ts` 생성**: 다섯 라우트는 커널 고정 목록이고, `Feature.nav` 는 그 안의 **하위 섹션**(예: fo 가 「오늘」레인 카드·「계좌」파생 보유·「시스템」`/system/fo` 항목)을 `{screen, id, label, order}` 로 기여한다. 빌드 시점 정적 파일이며 "기능 켜짐/꺼짐" 개념은 없다. 실행 중 비어 있는 것(fo 계좌 없음, `scalp.enabled=false`)은 **데이터가 비어 있으면 화면이 섹션을 숨기는** 방식으로 처리한다(`accounts` 응답의 레인 목록·`system/health.features` 를 근거로). CI 동기 테스트는 sse-keys 와 같은 방식.
8. **`settings.web`**: `tailscale_hosts`(Host 허용 목록)·`tailscale_users`(허용 로그인, 비어 있으면 전원)·`public_base`(알림 링크용) 추가. 기존 `WebDeps.extra_hosts` 는 `tailscale_hosts` 로 채운다. 템플릿에 자리표시자.
9. 승인 처리 공통 함수 **spot `approvals.decide(ctx, approval_id, action, *, channel, actor, confirm)`** 하나로 웹 PATCH·텔레그램 버튼·텍스트 명령이 모두 들어간다. 실전이면 `confirm == account_id` 를 서버가 검사. 만료 조건(M4 Ruling 25)은 그대로.
10. **웹 쓰기의 집행 경로(웹 프로세스 분리에 따른 결정)**: 웹 프로세스에는 브로커·알림이 없다. (a) 승인: `PATCH /approvals/{id}` 는 원장에 `decided_by`·`channel`·`decided_at` 을 적고 spot 큐 kind **`approve_execute`**(fast lane, 멱등: 같은 승인 id 중복 요청 무시) 를 넣는다. 데몬이 수 초 안에 `executor.approve` 를 실행하고 SSE `approvals`·`orders` 키가 바뀐다. 텔레그램 버튼은 데몬 안이므로 큐 없이 즉시 집행. (b) 모드·일시정지·감시목록·스로틀: 원장 control 직접 쓰기(기존과 같음; 데몬은 매 틱 읽음). (c) 잡 수동 실행: 기존 `job_requests` 큐. (d) 시세 허브의 "체결통보 리스너가 앱키 세션을 쥠" 신호: 데몬 리스너가 control 플래그 `ws_session_busy:<appkey_hash>` 를 TTL 과 함께 쓰고 웹 허브가 읽는다(옛 `blocked()` 의 프로세스 간 판). (e) 웹이 보내는 알림은 없다(알림은 전부 데몬). `notices` 기록은 데몬 notify 가 한다. (f) 웹 프로세스의 KIS 시세용 토큰은 커널 broker 의 공유 토큰 캐시(`TRADER_KIS_SHARED_DIR` 와 같은 파일) 를 데몬과 함께 쓴다.
11. **M11 범위에서 제외(비웹 이연 항목)**: 앞선 마일스톤이 "M11" 로 미룬 비웹 항목(`InputsPort.recent`, `queue_jobs` 분리·`_hydrate` 공개, 커널 승격 후보 4종, `auto_classify` 호출자, spot monitor 단타 보유분 검토, `AccountsPort` 가짜 포트 정리, regime `_turn`)은 이 설계의 범위가 아니다. M11 플랜은 TODO 의 해당 줄을 **M12** 로 옮기고, 웹이 직접 필요로 하는 것(`metrics.pending_approvals`·`account_summary` 류 집계) 만 자원 구현의 일부로 포함한다.

## 9. 프런트 구조(M11b)

```
web/src/
  main.tsx  app/App.tsx  app/router.tsx  app/shell/ (StatusBar, Clock, Nav, ThemeToggle)
  lib/api.ts (fetch + 토큰 + X-Server-Time)  lib/sse.ts (재연결·폴링 폴백)  lib/format.ts (금액·시각)
  lib/glossary.ts  lib/features.ts  lib/orders.ts  (옛 순수 함수 이식, vitest)
  generated/sse-keys.ts  generated/nav.ts
  screens/today/  screens/accounts/  screens/performance/  screens/data/  screens/system/  screens/decision/
  components/ (Card, Table→카드 전환, Badge, Confirm(실전 재입력), Sparkline, Chart(lightweight-charts))
  styles/tokens.css  styles/base.css
public/manifest.webmanifest  public/icons/
```
- 상태 관리는 fetch 캐시 + SSE 무효화(라이브러리 없음). 라우트 다섯 개 + 결정 상세.
- 반응형 기준: 1열(<640px) / 2열(<1024px) / 메뉴+본문(≥1024px). 표는 640px 미만에서 카드로 바뀐다(열 숨김 금지).
- 디자인 체계(색·글자·간격 토큰, 상태색 3단계, 실전 색, 다크)는 M11b 첫 Task 에서 `ui-ux-pro-max` 로 정해 `tokens.css` 와 짧은 `web/DESIGN.md` 에 고정; 이후 화면은 그 부품만 조합. 대시보드 다듬기는 `design-taste-frontend` 로 한 번.
- 테스트: vitest 순수 함수 5종 이식 + 화면별 렌더 테스트(jsdom, 합성 응답) 각 1~2개. `npm run typecheck && npm test -- --run && npm run build` 가 CI.

## 10. 순서와 완료 기준

- **M11(백엔드, PR 1개)**: 자원을 화면 우선순위로 — ① 오늘(decisions·orders·approvals·notices·jobs 일부·events 키·health) ② 계좌·보유(accounts·positions·fx·control_log·settings/paused) ③ 시스템(jobs 전체·runs·calls·usage·secrets·bootstrap) ④ 성과(analytics) ⑤ 데이터(news·quotes·charts·names·features). 완료: 12 자원 전부 + 옛 대응표 100% + 옛 `tests/web` API 테스트 이식 + SSE/nav 생성 파일 동기 테스트 + 마이그레이션 15 + 텔레그램 버튼 왕복 테스트(가짜 수신).
- **M11b(프런트, PR 1개)**: 같은 순서로 화면. 완료: 다섯 화면 + 결정 상세가 합성 API 로 렌더되고 휴대폰 폭(390px)에서 가로 스크롤 없음, PWA 설치 가능, vitest·typecheck·build 녹색.
- 사용자 확인(실기): tailnet 휴대폰에서 조회 → 토큰 저장 → 모의 계좌 일시정지/재개 반영 → approve 모드에서 텔레그램 버튼 승인 → 주문 접수 확인(TODO 「사용자 확인 대기」 기존 항목을 갱신). **시점은 M12 전환 뒤**: 리허설 중 새 데몬은 `notify.enabled=false` 라 같은 봇의 `callback_query` 가 옛 데몬으로 가므로, M11 에서는 가짜 수신기로 버튼 왕복을 테스트하고 실기는 전환 뒤에 한다.

## 11. 빌더에 맡기는 것

- 각 자원의 정확한 pydantic 응답 모델 이름·페이지네이션 방식(`limit/offset` 또는 커서) — 자원 내 일관되면 됨.
- `merge.py` 의 정렬·중복 처리 세부, 부분 응답 실패 시 다른 기능 응답은 살리고 `partial: true` 표시.
- 상태 띠 임시 판정의 임계값(연속 실패 횟수·상한 임박 비율).
- 환율 조회 KIS 엔드포인트 선택과 캐시 시간(하루 1회면 충분).
- 차트 기간 기본값·분봉 지원 범위, 손절·목표선 표시 방식.
- CSS 토큰 값·아이콘 세트(SVG 인라인, 라이브러리 금지)·PWA 아이콘.
- 텔레그램 메시지 편집 실패(오래된 메시지) 시 처리.

## 12. 열어 둔 것(사용자 몫, 비차단)

- `web.public_base`(알림 링크의 호스트명)·`tailscale_hosts`·`tailscale_users` 는 M12 설정 때 사용자가 채운다.
- 운영 이상 알림의 종류·쿨다운 값은 M12 `kernel/ops` 설계 때 확정한다.
- 휴대폰 접근 방식: 사용자 확인(2026-11-04) — **`tailscale serve` 경유, 포트 없는 https, MagicDNS 이름**(`<호스트>.<tailnet>.ts.net` 형태). 따라서 `tailscale_users` 검사가 유효하고, 설정값은 M12 때 사용자가 채운다. 직접 IP 접속은 지원하되 문서에 "조회가 tailnet 전체에 열림" 을 명시한다(§3).

## 13. 빌더 검토 뒤 내가 정한 것(2026-11-04)

검토자 질문 9건 중 사용자 몫은 접근 방식 1건(§12), 나머지는 아래로 정했다.
1. 실전 승인 버튼은 2단계, 모의는 1단계; 서버가 `confirm` 강제(§5).
2. 웹은 데몬과 분리된 프로세스, 집행은 데몬 큐(§3·§8.10).
3. `approvals` 자원·텔레그램 콜백은 spot 소유, 커널에 `Feature.telegram_callbacks` 신설(§7·§8.5·§8.9).
4. 실전 쓰기 동작의 정의와 서버 강제 범위(§5).
5. 옛 `tailscale_users` 허용 목록 복원(§3·§8.8).
6. 결정 주소 `/decisions/{asset}/{id}`, spot 상세는 결정(하루) 단위 + 라운드 펼침(§6.1).
7. 비웹 이연 항목은 M11 범위 밖 → M12 로 이동(§8.11).
8. `nav.ts` 는 정적 하위 섹션, 숨김은 데이터 기준(§8.7).
9. 텔레그램 버튼 실기 확인은 M12 전환 뒤(§10).
