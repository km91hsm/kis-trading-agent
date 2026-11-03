# ml

## 책임

- `ml.yaml`(`label`·`train`·`models`)과 `schedule.yaml` 의 `ml_train`·`universe_members` 를 섹션으로 등록한다.
- 원장 v12 `universe_members`(시점 기준 유니버스 구간)를 소유한다.
- 피처 스토어(`state/features/<date>.parquet`, 키 (date, ticker), 쓰기는 멱등)와 학습 데이터셋(`dataset`: 적재·라벨·피처 열 선택)을 소유한다. 모델·학습·잡·CLI·포트는 뒤 Task 에서 더한다.

## 규약

- 피처 파일 쓰기는 `_store_lock` 아래 원자 교체. 룩어헤드 방지: 재무비율은 Y 값을 Y+1 년 4월 1일부터, 과거 asof 스냅샷은 `MAX_PAST` 넘으면 거부.
- 코드는 `kernel/` 과 `ingest.api` 만 import 한다(3층 규칙).

## 알려진 차이 (옛 저장소 대비)

- `_features_dir` 는 읽기 경로에서도 `state/features` 디렉터리를 만든다(옛 결함 보존).
- (피클 경로 관련 항목은 Task 13 에서 채운다.)
