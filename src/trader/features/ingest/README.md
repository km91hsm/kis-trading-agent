# ingest

## 책임

- 외부 수집원(KIS 시세·뉴스·거시·DART·EDGAR·커뮤니티·채널)의 설정과 분석 유니버스·섹터 맵을 소유한다. `sources.yaml` 의 7키(`kis news macro dart edgar community channels`), `universe.yaml`, `sectors.yaml`, `schedule.yaml` 의 `news_collect` 를 섹션으로 등록한다.
- 원장 v3 `news_items`(시장 전체 뉴스 저장소)를 소유한다.
- `base.py`: 수집 결과 `Section` 래퍼(`run_section`·`skipped_section`)와 공용 헬퍼.
- 수집기·잡·CLI 는 이후 단계에서 더한다(골격 단계).

## 공개면 (`api.py`)

`Universe`, `UsSymbol`, `SectorMap`, `load_universe`, `load_sectors`.

## 규약

- `EdgarCfg.user_agent` 는 비워 두고 `secrets/.env` 의 `EDGAR_USER_AGENT` 를 쓴다(공개 저장소에 연락처를 두지 않는다).
- 유니버스·섹터의 교차 필드 검증은 `load_universe`·`load_sectors` 가 전체 모델로 한다. 커널 섹션 등록은 템플릿 로드·키 검증용이다.
