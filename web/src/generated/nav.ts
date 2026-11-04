// 자동 생성 파일 — 수정 금지. `uv run trader dev gen-nav` 로 다시 만든다.
export const NAV = {
  "today":{"label":"오늘","path":"/","items":[{"screen":"today","id":"approvals","label":"승인","order":10},{"screen":"today","id":"spot-lane","label":"현물","order":15},{"screen":"today","id":"scalp-lane","label":"단타","order":20},{"screen":"today","id":"fo-lane","label":"파생","order":30}]},
  "accounts":{"label":"계좌·보유","path":"/accounts","items":[{"screen":"accounts","id":"overview","label":"통합","order":0},{"screen":"accounts","id":"fo-positions","label":"파생 보유","order":20}]},
  "performance":{"label":"성과","path":"/performance","items":[]},
  "data":{"label":"데이터","path":"/data","items":[{"screen":"data","id":"quotes","label":"시세·차트","order":0},{"screen":"data","id":"news","label":"뉴스·이벤트","order":10},{"screen":"data","id":"themes","label":"테마·감시목록","order":20},{"screen":"data","id":"features","label":"지표·모델","order":30}]},
  "system":{"label":"시스템","path":"/system","items":[]}
} as const;
