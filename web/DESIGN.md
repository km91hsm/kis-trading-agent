# 디자인 체계 (M11b)

개인용 자동매매 운영 대시보드 — 휴대폰·PC 겸용, 한국어, 표가 많은 고밀도 화면.
ui-ux-pro-max 조사(design-system · color · ux) 결과에서 고른 방향을 적는다. 값은 `src/styles/tokens.css` 가 원본.

## 방향
- 스타일: 차분한 슬레이트 중립 바탕 + 의미색만 채색(Financial Dashboard 팔레트 계열). 장식·그라데이션·글로우 없음.
- 테마: 라이트·다크 둘 다 1급. 기본은 OS 설정, 토글로 고정(`localStorage` 키 `trader.theme`, `<html data-theme>`).
- 글꼴: Pretendard(조사 추천 Fira 는 한글 미지원이라 대체). 기본 16px, 줄 1.5. 숫자는 `.num`(`tabular-nums`, 줄바꿈 없음). 코드·ID 는 `--font-mono`.
- 글꼴 로딩: `pretendardvariable-dynamic-subset.css`(가변 1벌, unicode-range 조각 woff2 — 쓰는 범위만 받음, 빌드 용량 축소).
- 아이콘: 인라인 SVG 만. 이모지 아이콘 금지.
- 줄바꿈: `body` 에 `word-break: keep-all` + `overflow-wrap: anywhere` — 한국어는 어절 단위로 꺾고(「접/수」 방지) 긴 영문 id 만 강제로 자른다.
- 맨 폼 컨트롤: 클래스 없는 `input`·`select`·`textarea` 는 `base.css` 의 `:where()` 기본(높이 `--touch`, 테두리 `--fg-muted`, 바탕 `--bg-elev`), 클래스 없는 `button` 바탕은 투명 — 브라우저 기본 회색(다크에서 대비 2:1)을 쓰지 않는다. 특이도 0 이라 부품·화면 규칙이 이긴다.

## 색 토큰 (라이트 / 다크) — 글자 대비는 `--bg` 기준(`--bg-elev` 위도 4.5:1 이상)
| 토큰 | 라이트 | 대비 | 다크 | 대비 | 용도 |
|---|---|---|---|---|---|
| `--fg` | #0F172A | 17.1 | #F1F5F9 | 17.2 | 본문 |
| `--fg-muted` | #475569 | 7.2 | #94A3B8 | 7.3 | 보조 글자 |
| `--accent` | #1D4ED8 | 6.4 | #60A5FA | 7.4 | 링크·주 버튼·포커스 |
| `--ok` / `--warn` / `--fail` | #166534 / #92400E / #B91C1C | 6.8 / 6.8 / 6.2 | #4ADE80 / #FACC15 / #F87171 | 10.8 / 12.3 / 6.8 | 상태 3단계 |
| `--up` / `--down` | #C62828 / #1D4ED8 | 5.4 / 6.4 | #FF6B6B / #60A5FA | 6.8 / 7.4 | 상승 적·하락 청(한국 관례) |
| `--live` on `--live-bg` | #C2410C / #FFF4EC | 4.8 | #FB923C / #431407 | 6.9 | "실전" 표식(주황) |
- 상태 배지 바탕 `--ok-bg`·`--warn-bg`·`--fail-bg` 위 글자 대비: 라이트 6.5/6.4/5.3, 다크 8.6/9.5/5.8.
- 색만으로 뜻을 전하지 않는다: 상태·등락에는 글자(정상/주의/실패, ▲▼ 또는 +/−)를 함께 쓴다.
- hex 는 `tokens.css` 에만. 컴포넌트·base.css 는 변수만.
- 컨트롤 테두리(입력·버튼)는 `--fg-muted` 또는 전용 테두리 토큰을 쓴다. `--line`(라이트 `--bg` 위 약 1.4:1)은 WCAG 1.4.11 의 3:1 미달이라 구분선 전용.
- 예외: `index.html` 의 `theme-color` 두 값(#F8FAFC/#0B1120)은 `--bg` 라이트/다크를 그대로 옮긴 것 — 함께 바꾼다.

## 간격·모양·모션
- 간격 4px 격자: `--space-1/2/3/4/6/8` = 4/8/12/16/24/32px.
- 반지름 `--radius` 6px 하나. 그림자 `--shadow` 1단계(떠 있는 메뉴·시트에만).
- 모션 `--motion` 150ms, 전환은 색·투명도 위주. `prefers-reduced-motion: reduce` 면 전환·애니메이션 제거(base.css).
- 터치 대상 최소 `--touch` 44px. 포커스 링 `:focus-visible` 2px `--focus` 항상 유지.
- 겹침: `--z-sticky` 10(고정 머리줄), `--z-overlay` 100(시트·대화상자).

## 반응형·표
- 확인 폭: 375 · 768 · 1024 · 1440px. 모바일 우선.
- 넓은 표는 좁은 폭에서 행을 카드로 바꾸거나 가로 스크롤 래퍼에 담는다(표가 화면을 밀어내지 않게).
- 열은 숨기지 않는다. 화면 고유 표(지표·호가)는 좁은 폭에서도 `.table-scroll` 가로 스크롤로 남기고, 전역(`html`·`body`·본문)에 `overflow-x: hidden` 을 두지 않는다(`screens/__tests__/responsive.test.tsx` 가 고정).
- 상단 상태 띠(<1024): 첫 줄 건강 요약 + 테마 토글, 다음 줄 사용률·시계. 좌우 가장자리 16px(본문과 같음).
- 공통 부품(`components/`, 스타일 `components.css`): 표는 `Table`(≥1024px `<table>` · 미만 `RowCard`, `useWide`), 쓰기 확인은 `Confirm`, 잡 실행은 `RunJobButton`.

## 메뉴·앵커
- 화면 안 섹션 컨테이너는 `id={item.id}`(자동 생성 `generated/nav.ts` 의 id)를 단다 — 하위 목차가 `<path>#<id>` 로 스크롤하고 `IntersectionObserver` 로 현재 섹션을 표시한다.
