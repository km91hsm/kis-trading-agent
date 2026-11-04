# ml

## 책임

- 설정: `ml.yaml`(`label`·`train`·`models` — `models.quant`·`ranker`·`calibration`·`rl_sizer`)과 `schedule.yaml` 의 `ml_train`(`MlTrainSchedule`, 기본 토요일 10:00 KST)·`universe_members`·`features_snapshot`(문자열 시각)을 섹션으로 등록한다.
- 원장 v12 `universe_members`(시점 기준 유니버스 구간)를 소유한다. 기록·조회·학습 데이터 필터링은 `universe_pit.py`(`record`·`members`·`apply`·`effective_day`, 편출 유예 `GRACE` 5일).
- 피처 스토어(`state/features/<date>.parquet`, 키 (date, ticker), 한 날짜 한 파일, 쓰기는 멱등)와 학습 데이터셋(`dataset`: 적재·라벨·피처 열 선택·`close_table`)을 소유한다(`features.py`).
- 모델(`models/quant.py` `QuantModel`, 백엔드 sklearn 기본·`lightgbm` 선택)·워크포워드 평가(`walkforward.py`)·학습(`train.py` `train_quant`, 승격 판정 `promotion_decision`)·튜닝(`tune.py` `tune_quant`)·레지스트리(`registry.py`).
- `05_quant` 리포트(`quant_report.py`: `latest_row`·`build`·`signal_for`, 스키마는 커널 `session.schemas.QuantReport`, `role="quant"`).
- 잡·큐(`jobs.py`): 잡 `ml_train`(주간 재학습, heavy)·`universe_members`(매일 06:40 KST 기록)·`features_snapshot`(매일 06:30 KST 피처 스토어 스냅샷, heavy), 큐 kind `features_backfill`·`features_snapshot`·`features_derive`·`ml_train`(전부 slow 레인), 훅 `daemon.bootstrap` → 시세 클라이언트가 있는데 피처 스토어가 비어 있으면 `features_backfill`(3년)+`ml_train` 을 큐에 넣는다(백필이 24시간 안에 실패했으면 다시 넣지 않음, 어떤 오류도 올리지 않음).
- 포트(`ports.py`): `ports["ml"]` = `MlPortImpl`(커널 `MlPort` — 피처 행·퀀트 리포트·피어 중앙값 `peer_medians`·모델 상태). 다른 기능은 `api.py` 만 import 한다.
- CLI(`cli.py`, `uv run trader ml --help`): `ml train|tune|models|promote|demote`, `ml features snapshot|backfill|derive`.
- 웹 자원(`routes.py`, 토큰 없음, `ml.api` 이름만 사용): `GET /features/latest?market=` → `{market, asof, columns, rows}`(종목별 최신 행 + `sectors.yaml` 섹터, NaN → null, 깨진 sectors.yaml 은 섹터만 비움), `GET /features/history?market=&ticker=&days=120`(5~1000, 유니버스에 없는 종목 404), `GET /ml/models` → `{models: [요약], active, note}`, `GET /ml/models/{version}`(`YYYYMMDD_HHMMSS` 아니면 400, 없으면 404) → 메타 전체. SSE 키 없음. 메뉴 `data/features "지표·모델"`. `api.ticker_history` 를 공개면에 더했다.

## 규약

- 피처 파일 쓰기는 `_store_lock`(파일 락) 아래 임시 파일 → 원자 교체이고, 같은 (date, ticker) 행은 덮어써서 멱등이다. 읽기는 락 없이 부분 읽기(`latest_rows(asof)`: asof 이하 최신 행만).
- 룩어헤드 방지(PIT): 재무비율은 Y 값을 Y+1 년 4월 1일부터 쓰고, 과거 asof 스냅샷은 `MAX_PAST` 를 넘으면 거부한다. 학습 라벨 엠바고(워크포워드가 마지막 학습일을 test 시작 `horizon_days + embargo_days + 1` 거래일 앞으로 당긴다)를 두며, 유니버스는 그날 구성원 이력(`universe_members`)으로 거른다(`ml.train.pit.strict` 면 기록 전 과거 행을 아예 뺀다, `liquidity_top_n` 으로 유동성 상위만).
- 승격 기준(`promotion_decision`): 공통 — 평가 폴드가 있고 모든 폴드의 up 예측 ≥ `min_up_predictions`("전부 중립" 모델 차단). `promote_metric=accuracy` 면 합산 정확도 > 기준선(최빈 라벨)·≥ `promote_min_accuracy`·알파 > `min_alpha`, `alpha`(scan 랭커용) 면 상위 N 평균 알파 ≥ `min_alpha`·알파 양인 폴드 비율 ≥ `promote_min_positive_folds`. `min_alpha` 는 시장별(`promote_min_alpha_by_market`) 값이 우선. 기준을 통과한 학습만 자동 승격하고, 아니면 저장만 한다.
- `ml tune` 은 리포트만 낸다: dev 폴드(마지막 폴드 제외)로 하이퍼파라미터를 고르고 홀드아웃을 보고한다. 설정·레지스트리를 바꾸지 않는다(적용은 사람이 `ml.yaml` 수정).
- 레지스트리 레이아웃: `state/models/<name>/<버전 YYYYMMDD_HHMMSS>/{model.joblib, meta.json}`(`name` = `quant`). `meta.json` 에 `promoted`·`promote_reason`·지표·학습 시장 등. `latest(promoted_only=True, market=)` 은 시장 전용 모델을 우선하고 없으면 전시장 모델을 쓴다. `latest` 는 깨진 pickle·클래스 변경으로 못 읽는 버전을 경고 후 건너뛴다(파이프라인 보호). `list_models` 는 `meta.json` 만 읽는다(못 읽는 메타는 조용히 건너뛴다).
- lightgbm 은 선택 의존(`extras`)이다. 이 맥처럼 libomp 가 없으면 `import lightgbm` 이 ImportError 가 아니라 OSError 라 `models/quant.py` 의 가용성 검사가 `except Exception` 으로 감싸고 기본 백엔드는 sklearn 이다(Ruling L1). lightgbm 으로 학습한 모델을 lightgbm 없는 환경에서 못 읽으면 경고(`ml demote` 안내) 후 건너뛴다.
- 코드는 `kernel/` 과 다른 기능의 `api.py`(`ingest.api`·`regime.api`) 만 import 한다(3층 규칙). 로거 `trader.ml`.

## 알려진 차이 (옛 저장소 대비)

- `QuantModel` 의 pickle 모듈 경로가 바뀌었다(`trader.features.ml.models.quant`). 옛 `state/models/quant/*` 는 로드할 수 없다 — `latest` 가 경고 후 건너뛰므로 크래시는 없고, 새로 `trader ml train` 으로 재학습해야 `05_quant`·ML 랭킹이 살아난다.
- `_features_dir` 는 읽기 경로에서도 `state/features` 디렉터리를 만든다(옛 결함 보존, Ruling P2).
- `promote` 는 다른 버전을 내리지 않는다(옛 동작). 승격 버전이 여럿이면 `latest` 가 가장 최근 것을 쓴다. 정리는 `trader ml demote <version>` 으로 사람이 한다.
- `ml.yaml` 의 `rl_sizer`·`calibration` 절은 설정만 있고 소비자가 없다(M8). RL 사이저(`ml/rl/*`·`rl_shadow`·`rl_train` kind·`trader rl`)도 M8.
- 피처 스토어 일일 스냅샷은 `features_snapshot` cron(06:30 KST, 매일)이 한다 — M8 의 reflect `snapshot_fn` 이 가져가기 전까지(`MlPortImpl.snapshot` 은 포트 구성이 모자라면 RuntimeError).
- `registry.promote`/`demote` 는 벽시계 시각을 도장으로 찍는다(옛 결함 보존, Ruling P2).
- 옛 최상위 `trader features …` 는 `trader ml features …` 가 되었다.
- 스케줄 키 모양: `schedule.yaml ml_train: {time: "10:00", days: sat, tz: null}`(옛 `ml_train_time`/요일 평면 키 아님), `features_snapshot: "06:30"`·`universe_members: "06:40"`(문자열, KST).
- `peer_medians` 반환은 옛 `_peer_section` 모양(`sector, n, dates, median, mine, diff`)을 유지한다(Ruling T6-1).
- 커널 runner 는 `daemon.bootstrap` 훅을 `bootstrap_queue`(stale·만료 정리) 뒤에 부른다(옛과 같은 정리 → enqueue 순, Ruling F2).
- `ml_config`/`load_ml` 은 호출마다 레지스트리를 다시 만든다(성능 이슈 없음, 미최적화).

- M8 이후: `close_table` 주입(`MonitorContext`·`MlPort.close_table`)이 살아 있고, `ROUND_TRIP_BP` 중복은 커널 `execution/costs.py` 로 해소됐다. RL 사이저는 evaluation `rl/` 로 옮겼다(`trader evaluation rl …`, `ml_trained` 문구는 evaluation 이 쓴다).
