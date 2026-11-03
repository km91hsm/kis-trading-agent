# briefing

## 책임

- 아침 브리핑(tier=service): 평일 07:20 KST 에 haiku 단발 1회(`kernel.session.quick.ask_json`, 에이전트 `briefing`) → 텔레그램 5줄. 사람이 읽는 요약 전용 — 결정·게이트·주문은 읽지 않는다.
- 설정: `settings.yaml briefing`(`enabled`·`daily_cap` 0~5·`timeout_s`), 시각은 `schedule.yaml briefing`(KST). 모델은 `models.briefing: haiku`.
- 입력(`briefing.build_inputs`): `date`(KST)·`us_overnight`(지수 proxy 일봉 전일 대비)·`regime`·`market_view`·`news`(12시간·시장당 15건)·`calendar`(세이브티커 2일, ≤10)·`holdings`(비중>0). 다른 기능은 `evaluation`·`regime`·`ingest`·`saveticker` 의 `api` 로만 쓴다.
- `run`: 꺼짐 → `{"skipped": "꺼짐"}`, 구독 감속 → `{"skipped": "감속: …"}`, 상한·실패 → `{"skipped": "판정 없음(상한·실패)"}`, 성공 → `{"lines": n}`. FAKE_CLAUDE=1 이면 고정 1줄.
- 잡 `morning_briefing`(`per_market=False`, cron mon-fri): 본문이 KR 시장 여부·일시정지·KR 휴장일을 직접 보고 건너뛴다(`{"skipped": …}`, LLM 호출 없음). CLI 없음.

## 알려진 차이

- 옛 `orchestrator/briefing` 의 5줄 형식은 유지하되 `quick.ask_json`(`agents.with_schema` 재사용, T2-R1)으로 부른다. CLI 는 없다(잡 `morning_briefing` 만).
- A9: 시각 키가 `settings.briefing.time` 에서 `schedule.yaml briefing: "07:20"`(KST)로 옮겨졌다. `enabled`·`daily_cap`·`timeout_s` 는 `settings.yaml briefing:` 에 남는다. 기존 `config/settings.yaml` 의 `briefing.time` 은 읽지 않으니 `schedule.yaml` 로 옮긴다.
- `morning_briefing` 은 KR 전용 가드를 본문에서 직접 건다(`per_market=False`; 본문이 KR 휴장일·일시정지를 직접 본다 — 옛 `market_job(deps,"KR",…)` 와 같은 효과).
- 텔레그램 `/daily` 는 evaluation 이 소유한다(briefing 아님).
