# 디자인 체계 (M11b · M13 다듬기)

개인용 자동매매 운영 대시보드 — 휴대폰·PC 겸용, 한국어, 표가 많은 고밀도 화면.
ui-ux-pro-max 조사(design-system · color · ux)에서 고른 방향 위에 M13 에서 design-taste-frontend 앱 화면 규칙(`data-terminal` 스타일, 그라데이션·글로우·아이콘 타일 없음)으로 밀도와 팔레트를 다듬었다. 값은 `src/styles/tokens.css` 가 원본.

## 방향
- 스타일: 차분한 중립 바탕 + 의미색만 채색. 장식·그라데이션·글로우 없음. 카드는 큰 반지름(14px)·얇은 선, 칩은 테두리 없는 알약(색은 바탕 틴트).
- 테마: 라이트·다크 둘 다 1급. 기본은 OS 설정, 토글로 고정(`localStorage` 키 `trader.theme`, `<html data-theme>`).
- 글꼴: Pretendard(조사 추천 Fira 는 한글 미지원이라 대체). 숫자는 `.num`(`tabular-nums`, 줄바꿈 없음). 코드·ID 는 `--font-mono`.
- 글자 스케일(운영 대시보드 밀도): `--text-xs` 12(표 머리·칩·라벨) · `--text-sm` 13(보조·표 본문·메뉴 하위) · `--text-md` 14(본문·섹션 제목) · `--text-lg` 16(브랜드) · `--text-xl` 20(화면 제목) · `--text-kpi` 22(합계·성과 큰 숫자). 줄 1.5.
- 글꼴 로딩: `pretendardvariable-dynamic-subset.css`(가변 1벌, unicode-range 조각 woff2 — 쓰는 범위만 받음, 빌드 용량 축소).
- 아이콘: 인라인 SVG 만. 이모지 아이콘 금지.
- 줄바꿈: `body` 에 `word-break: keep-all` + `overflow-wrap: anywhere` — 한국어는 어절 단위로 꺾고(「접/수」 방지) 긴 영문 id 만 강제로 자른다.
- 맨 폼 컨트롤: 클래스 없는 `input`·`select`·`textarea` 는 `base.css` 의 `:where()` 기본(높이 `--touch`, 테두리 `--fg-muted`, 바탕 `--bg-elev`), 클래스 없는 `button` 바탕은 투명 — 브라우저 기본 회색(다크에서 대비 2:1)을 쓰지 않는다. 특이도 0 이라 부품·화면 규칙이 이긴다.

## 색 토큰 (라이트 / 다크) — 글자 대비는 `--bg` 기준(`--bg-elev` 위도 4.5:1 이상)
| 토큰 | 라이트 | 대비 | 다크 | 대비 | 용도 |
|---|---|---|---|---|---|
| `--bg` / `--bg-elev` / `--bg-sunken` | #F7F8FA / #FFFFFF / #EEF1F5 | | #0F1217 / #161B23 / #0B0E12 | | 바탕 / 카드·띠 / 움푹(기본 칩·펼친 행) |
| `--fg` | #14181F | 16.7 | #E6E9EF | 15.4 | 본문 |
| `--fg-muted` | #5B6472 | 5.6 | #9AA4B2 | 7.4 | 보조 글자 |
| `--accent` | #1F5FD0 | 5.5 | #6FB1FF | 8.4 | 링크·주 버튼·포커스·현재 하위 항목 |
| `--ok` / `--warn` / `--fail` | #1E6B3A / #8A4B0F / #B42323 | 6.1 / 6.4 / 6.2 | #5FD38A / #F2C14E / #FF7A7A | 10.0 / 11.2 / 7.4 | 상태 3단계 |
| `--up` / `--down` | #C9302C / #1F5FD0 | 5.0 / 5.5 | #FF7A7A / #6FB1FF | 7.4 / 8.4 | 상승 적·하락 청(한국 관례) |
| `--live` on `--live-bg` | #B3420F / #FDF0E7 | 4.8 | #FFA05C / #3F2314 | 7.1 | "실전" 표식(주황) |
- 상태 칩 바탕 `--ok-bg`·`--warn-bg`·`--fail-bg` 위 글자 대비: 라이트 5.7/5.9/5.4, 다크 6.5/6.5/5.3. 칩은 테두리가 없으므로 이 대비가 전부다.
- 색만으로 뜻을 전하지 않는다: 상태·등락에는 글자(정상/주의/실패, ▲▼ 또는 +/−)를 함께 쓴다.
- hex 는 `tokens.css` 에만. 컴포넌트·base.css 는 변수만.
- 컨트롤 테두리(입력·버튼)는 `--fg-muted` 또는 전용 테두리 토큰을 쓴다. `--line`(라이트 `--bg` 위 약 1.4:1)은 WCAG 1.4.11 의 3:1 미달이라 구분선 전용.
- 예외: `index.html` 의 `theme-color` 두 값(#F7F8FA/#0F1217)은 `--bg` 라이트/다크를 그대로 옮긴 것 — 함께 바꾼다.

## 간격·모양·모션
- 간격 4px 격자: `--space-1/2/3/4/6/8` = 4/8/12/16/24/32px.
- 반지름 셋: `--radius` 8px(컨트롤·입력·빈 상태·차트 자리), `--radius-card` 14px(카드·행 카드·대화상자·합계 띠), `--radius-chip` 999px(칩·배지·사용률 칩). 그림자 `--shadow` 1단계(떠 있는 메뉴·시트에만).
- 모션 `--motion` 150ms, 전환은 색·투명도 위주. `prefers-reduced-motion: reduce` 면 전환·애니메이션 제거(base.css).
- 터치 대상 `--touch`: <1024 44px, ≥1024(마우스) 36px — 컨트롤 높이가 같이 줄어 밀도가 오른다. 상단 띠 높이는 `--bar` 44px 고정(메뉴 sticky·앵커 `scroll-margin` 이 이 값을 쓴다). 포커스 링 `:focus-visible` 2px `--focus` 항상 유지.
- 빈 상태(`Empty`)는 한 줄 약 38px(점선 테두리) — 빈 칸이 화면을 차지하지 않는다. 자료 없는 차트는 `Chart` 가 축 대신 같은 높이의 `.chart-empty` 자리만 남긴다.
- 겹침: `--z-sticky` 10(고정 머리줄), `--z-overlay` 100(시트·대화상자).

## 반응형·표
- 확인 폭: 375 · 768 · 1024 · 1440px. 모바일 우선.
- 넓은 표는 좁은 폭에서 행을 카드로 바꾸거나 가로 스크롤 래퍼에 담는다(표가 화면을 밀어내지 않게).
- 열은 숨기지 않는다. 화면 고유 표(지표·호가)는 좁은 폭에서도 `.table-scroll` 가로 스크롤로 남기고, 전역(`html`·`body`·본문)에 `overflow-x: hidden` 을 두지 않는다(`screens/__tests__/responsive.test.tsx` 가 고정).
- 상단 상태 띠: 상태 점·첫 사유·「N분 전 갱신」(`/system#health` 링크) · 지수·환율 칩(`IndexChips`, 장중만·`next_poll_ms` 폴링) · 사용률 칩 · 시계 · 테마 토글(「테마: 시스템/라이트/다크」 — 메뉴 「시스템」과 헷갈리지 않게 접두어를 붙인다). <1024: 첫 줄 건강 요약 + 테마 토글, 다음 줄 칩·시계. <768: 한 줄(상태 · 더 보기 ▾ · 시각 · 테마) — 지수·사용률 칩은 `details.statusbar-more` 안, 날짜·장 세션 글자는 숨긴다(`title` 로 남김). 좌우 가장자리 16px(본문과 같음).
- 공통 부품(`components/`, 스타일 `components.css`): 표는 `Table`(≥1024px `<table>` · 미만 `RowCard`, `useWide`), 쓰기 확인은 `Confirm`, 잡 실행은 `RunJobButton`, 라벨-값 격자는 `Stats`(`<dl class="stats" data-cols=auto|2|3|4 data-size=sm|kpi>`, <768 은 3·4열도 2열). 「값 · 값 · 값」 문자열 나열·`<dl>` 손제작·`<pre>` 덤프 대신 `Table`/`Stats` 를 쓴다. 시각은 `fmtWhen`(오늘 `HH:MM`, 다른 날 `MM.DD HH:MM`, 날짜만 `MM.DD`), 비율 필드(`*_pct` 가 0~1)는 `fmtPct`, 상태·국면·레인·환경 라벨은 `glossary` 로 통일한다. 행 카드의 제목 줄은 `long` 열 — 이름이 있는 표(계좌)는 그 열에 `long: true` 를 준다.
- 오늘: 절 순서는 승인 → 「오늘 요약」 KPI(`.today-summary`, `Stats size=kpi`: 결정·주문·체결·실패·알림) → 레인 카드(`Stats cols=3`: 결정·주문·다음 예정 = 데몬 사용자 잡 `next_at`) → 결정(종목 코드+이름 · 내용 = 최종 의견 요약, 0건이면 CSV 숨김) → 주문·체결 → 다음 할 일(`Table`: 사용자 잡만 `USER_JOB_KINDS`, 한글 라벨 + 시장 칩 · 예정 「HH:MM · N시간 뒤」 · 마지막 칩) → 알림(`Table` 30행 `Pager`: 시각·종류(본문 「[제목]」 → 칩)·계좌·내용 첫 줄(원문은 title)·전송 실패 칩; 필터 칩은 한글). 기록은 맨 아래, 전체 폭, 2열 묶음은 없다. 계좌·보유: 합계는 `.kpi-strip` 한 카드 3칸(원화·달러·환율, 세로선 구분; 768~1023 2열+환율 전폭, <768 1열) — 칸 라벨에 범위 「모의 n · 실전 n」, 금액은 `moneyUnit`(「10,000,000원」·「$2,500.50」). 전역 버튼은 대상을 적는다(「전체 자동매매 일시정지/재개」). 계좌 목록은 `Table`(카드 격자 아님; 헤더 「순자산」, 승인 대기 0건은 칩 대신 흐린 「없음」, 현물 계좌가 없으면 「현물 계좌 없음」). 파생 계좌 카드는 `/fo/summary`(fo_nav_snapshot) 의 NAV·증거금(NAV 대비 %)·상태 칩(`FO_STATE_LABEL`)을 쓰고, 실패하면 `/accounts` 의 NAV·현금으로 물러난다. 계좌 상세(`/accounts/:id`)는 h1 「계좌 {id}」 → 머리 카드 `.account-head`(모의/실전·레인 칩, `Stats cols=2` NAV·현금(갱신 시각 sub), 모드 select·적용·`Confirm` 을 카드 안에 — 따로 「모드」 절은 없다) → 오늘 주문·체결(상태는 `statusLabel` 한글 칩: 체결 ok·실패/거절/취소 fail, 수량 `fmtInt`) → 보유 → 단타 → 리스크(`Stats cols=3` VaR·CVaR·베타 + 「한도 대비」 `Table`[항목·현재·한도(최대/최소/손실 N)·판정 칩 정상/초과/미달/자료 없음] — 막대 없음, 위험 기여만 막대, 자료 부족은 `Empty`) → NAV 추이(`Chart` 는 점 1개면 「자료 1일 — 추이는 내일부터」).
- 왼쪽 메뉴(≥1280) 216px. 본문 여백 ≥1280 에서 24/32px.

## 메뉴·앵커
- 화면 안 섹션 컨테이너는 `id={item.id}`(자동 생성 `generated/nav.ts` 의 id)를 단다 — 하위 목차가 `<path>#<id>` 로 스크롤하고 `IntersectionObserver` 로 현재 섹션을 표시한다.
- 화면에 페이지가 여럿이면(시스템: 상태 `/system` · 잡 `/system/jobs` · 기능 `/system/features` · 관리 `/system/admin`, `nav.ts` `pages`) 2단 메뉴는 절 앵커가 아니라 페이지 링크(`aria-current="page"`)이고, 왼쪽 메뉴가 없는 폭(<1280)은 화면 위 `.page-tabs` 알약이 같은 일을 한다. 절이 어느 페이지에 가는지는 항목의 `page`(`gen.py SYSTEM_PAGES`). 백엔드가 주는 `/system#절` 링크는 `lib/nav.ts resolveLink` 로 페이지 주소로 바꾼다. 메뉴 항목을 더 쪼개지 않는다 — 새 절은 기존 페이지 안에.
- 긴 목록(실행 기록·조작 이력, 이후 알림·뉴스·공시·지표)은 `usePaged`(30행, `?page=N`) + `Pager`(「‹ 1 2 3 ›  31–60 / 65」). 한 페이지면 Pager 는 없다. 한 화면에 표가 둘이면 param 을 따로 준다(잡 페이지: 실행 기록 `?page`, 요청 기록 `?rpage`). 날짜·종류 필터를 바꾸면 `usePageReset()` 으로 1쪽으로 돌아간다.
- 하위 목차는 현재 화면 것만 펼친다. 화면이 섹션을 `hidden` 으로 숨기면(승인 0건·없는 레인·파생 계좌 없음) 목차에서도 빠진다(`Nav.tsx` 가 본문 DOM 을 MutationObserver 로 본다) — 죽은 앵커를 메뉴에 두지 않는다.
- 현재 항목은 방금 누른 항목(해시)이 우선이고, 사용자가 스크롤 입력(wheel·touchmove·방향키)을 하면 보이는 가장 위 섹션으로 넘어간다. 같은 항목을 다시 눌러도 스크롤한다(`location.key`).
- 왼쪽 메뉴(≥1280) 위에 브랜드 「trader」, 바닥에 「데몬 정상 · v0.2.0」 푸터(`/system/health`).
