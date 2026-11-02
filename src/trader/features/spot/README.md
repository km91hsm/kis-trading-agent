# spot

## 책임

- 현물(KR·US) 종목 분석 레인의 골격: 리스크·토론 설정(`settings.yaml` 의 `risk`·`debate_rounds`·`debate`), 에이전트 출력 스키마, 결정 저장소(원장 v6)를 소유한다.
- 원장 v6 테이블: `decisions`(날짜·시장·종목당 1행, 금액·비중은 TEXT), `decision_rounds`(결정 라운드별 행, 0 = 최초 결정, `31_decision_r<n>` 은 M5 재평가), `analyst_reports`, `debate_turns`, `ticker_names`, `ticker_exchanges`.
- 체크포인트(`10_analyst_<role>`, `20_debate_r<n>_<side>`, `30_decision`)를 `ledger.ingest_checkpoint` 가 멱등 적재한다.
- 세션·도구·잡·주문 출처는 뒤 Task 가 `feature.py` 에 더한다.

## 공개면 (`api.py`)

없음(`__all__ = []`). 레인은 다른 기능이 쓸 면을 두지 않는다. 같은 기능 안에서는 `schemas`(스키마·`schema_for`), `ledger`(결정 저장소), `labels`(`label`·`resolve`), `settings`(`RiskLimits`·`DebateCfg`)를 직접 쓴다.

## 규약

- 레인은 `trader.kernel.*` 만 import 한다. 계좌·수집 같은 서비스 기능은 `Boot.ports`(`accounts`·`inputs`)로 주입받는다(`registry.import_violations`).
- 스키마 모델은 `extra="forbid"`. 가격은 양수만, `target_weight` 는 0~0.25.
- 원장 쓰기는 호출마다 commit 하고 행 단위로 멱등이다(같은 단계 재적재 = 덮어쓰기, 행 id 유지). 로거는 `trader.spot`.
- `settings.yaml` 키를 더하면 `templates/config/settings.yaml` 도 같이 바꾼다.

## 테스트

`tests/features/spot/` — 설정·스키마·원장·기능 등록·포트 조립. 포트 구현체 테스트는 `tests/features/accounts/test_ports.py`, `tests/features/ingest/test_ports.py`.

## 알려진 차이 (옛 저장소 대비)

- 도구(`tools.py`·`tool_cli.py`): `trader tool status|collect|checkpoint list|read|write|bundle|summarize`. 쓰기 허용·전제조건은 `WRITE_RULES`(fnmatch glob) + 스키마·파일명↔페이로드 일치·`debate_rounds` 상한이다. `trader_context` 는 quote·risk_limits·costs_bp·portfolio·holding 만 싣는다(memory·calibration·quant·regime·theme·market_view 는 이연). `submit-decision` 은 뒤 Task.
