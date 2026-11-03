# briefing

## 책임

- 아침 브리핑(tier=service): 평일 07:20 KST 에 haiku 단발 1회(`kernel.session.quick.ask_json`, 에이전트 `briefing`) → 텔레그램 5줄. 사람이 읽는 요약 전용 — 결정·게이트·주문은 읽지 않는다.
- 설정: `settings.yaml briefing`(`enabled`·`daily_cap` 0~5·`timeout_s`), 시각은 `schedule.yaml briefing`(KST). 모델은 `models.briefing: haiku`.
- 입력(`briefing.build_inputs`): `date`(KST)·`us_overnight`(지수 proxy 일봉 전일 대비)·`regime`·`market_view`·`news`(12시간·시장당 15건)·`calendar`(세이브티커 2일, ≤10)·`holdings`(비중>0). 다른 기능은 `evaluation`·`regime`·`ingest`·`saveticker` 의 `api` 로만 쓴다.
- `run`: 꺼짐 → `{"skipped": "꺼짐"}`, 구독 감속 → `{"skipped": "감속: …"}`, 상한·실패 → `{"skipped": "판정 없음(상한·실패)"}`, 성공 → `{"lines": n}`. FAKE_CLAUDE=1 이면 고정 1줄.
- 잡 `morning_briefing`(`per_market=False`, cron mon-fri): 본문이 KR 시장 여부·일시정지·KR 휴장일을 직접 보고 건너뛴다(`{"skipped": …}`, LLM 호출 없음). CLI 없음.
