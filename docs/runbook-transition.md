# 전환 리허설·전환·되돌리기 런북 (M12, 사용자 수동 단계)

옛 저장소(`trading-agent`)의 데몬을 새 저장소(`kis-trading-agent`)로 바꾸는 절차다. 데몬 기동·로그온 등록·
주문 점검은 **사용자만** 실행한다(CLAUDE.md 금지사항). 명령은 소스 실행 기준 `uv run trader …` 이고, 번들이면
`./trader …`(Windows `trader.exe …`)로 읽는다. `<…>` 는 자리표시자다 — 실제 계좌번호·토큰·호스트를 이 문서나
커밋에 적지 않는다.

## 1. 사전 준비 (새 저장소)

```
cd <새 저장소>
uv sync
uv run trader init                                   # config/ secrets/ state/ docs/ 생성, 없는 템플릿만 복사
uv run trader accounts secrets import --from <옛 저장소>/secrets/.env   # 키 이름 매핑 복사, 값은 출력 안 함
uv run trader accounts ping                          # KIS 시세·휴장일·계좌별 현금(파생은 잔고 줄), 실패면 exit 1
uv run trader selfcheck                              # 설치 점검 JSON 1줄, ok=true 확인
```

- `config/accounts.yaml` 의 자리표시자 계좌번호를 본인 값으로 바꾼다(모의 `auto`, 실전 `off` 로 시작).
- **`WEB_TOKEN`**: `secrets/.env` 에 웹 쓰기 토큰을 둔다. `trader app` 은 첫 실행에 없으면 만들어 넣고
  (`config.secrets.ensure_web_token`), `trader web` 만 쓸 때는 직접 넣어야 쓰기 API 가 열린다
  (`WEB_TOKEN=<임의의 긴 문자열>`). 이 PC 브라우저는 시스템 화면 「이 PC 에서 받기」로 토큰을 받는다.
- (선택) 모의 주문 연결 점검: `uv run trader accounts order-test --account <모의 계좌 id>` — 1주 지정가
  (현재가 −10%) 접수 → 즉시 취소, 결과 JSON 1줄. 장중에만 의미가 있다.

## 2. 리허설 (옛 데몬은 그대로 둔다)

옛 데몬이 실제 운용을 계속하는 동안, 새 저장소는 알림·포트를 비켜 같은 PC 에서 **하루** 돌린다.

1. 새 저장소 `config/settings.yaml`:
   ```yaml
   notify:
     enabled: false     # 텔레그램을 옛 데몬과 나눠 쓰지 않는다 (NullNotifier, 수신기 없음)
   web:
     port: 8788         # 옛 웹(8787)과 겹치지 않게
   ```
2. KIS 토큰 캐시·레이트 DB 를 옛 데몬과 공유한다(앱키당 토큰 발급 제한):
   ```
   export TRADER_KIS_SHARED_DIR=<옛 저장소 state 경로>          # Windows: $env:TRADER_KIS_SHARED_DIR="<…>"
   ```
3. 데몬 기동 — 구독 호출·주문 없음:
   ```
   FAKE_CLAUDE=1 uv run trader daemon --dry-run
   ```
   웹은 다른 터미널에서 `uv run trader web --port 8788`.
4. **판정**(하루 뒤, 전부 만족해야 전환):
   - 웹 `/system`(또는 `GET /api/system/daemon`)의 잡 표에서 **등록 잡 전부**가 1회 이상 실행됐다
     (`last_start` 가 있음; 시장 게이트로 `skipped` 된 잡은 사유가 휴장·창 밖인지 확인).
   - `runs` 오류 0 — 웹 `/system` 최근 실행(`GET /api/runs`)에 `error` 없음.
   - `ops_check` 결과 `ok` — 웹 상태 띠/`GET /api/system/health` 의 `level` 이 `ok`, `uv run trader status` 의
     `state/STATUS.md` 「운영 점검」 절이 「점검 정상」.
   - `uv run trader watchdog --quiet` 출력 JSON 의 `level` 이 `ok`.
   - 대시보드(`http://127.0.0.1:8788`) 오늘·계좌·시스템 화면이 갱신된다(SSE 리더 탭, 시세 폴링).
   하나라도 어긋나면 원인을 고치고 리허설을 하루 더 한다.

## 3. 전환

1. 옛 저장소에서 로그온 등록 해제(설치기 label 이 새 저장소와 같아 남겨 두면 덮어쓴다):
   ```
   cd <옛 저장소>
   uv run trader install --uninstall
   uv run trader install --uninstall --watchdog        # 외부 감시를 등록했었다면
   ```
   옛 데몬이 아직 떠 있으면 내린다(옛 저장소 방식대로 — 실행 중 세션이 끝나길 기다린다).
2. 새 저장소 `config/settings.yaml` 을 원복: `notify.enabled: true`, `web.port: 8787`.
   `TRADER_KIS_SHARED_DIR` 는 지운다(새 저장소 `state/` 를 쓴다).
3. 로그온 등록:
   ```
   cd <새 저장소>
   uv run trader install                 # 데몬 (먼저 --dry-run 으로 plist/XML 내용 확인 가능)
   uv run trader install --watchdog      # 데몬 밖 5분 감시
   ```
4. 확인:
   - `uv run trader notify-test` — 텔레그램 시험 메시지 1건.
   - 텔레그램 `/status` — 「운영 점검」·「데몬」 절과 계좌 절이 나온다.
   - 웹 `http://127.0.0.1:8787/system` — 데몬 살아 있음·운영 점검 정상·잡 표 다음 예정 시각.

## 4. 번들 (선택)

```
git tag v0.2.0 && git push origin v0.2.0
```
→ GitHub Actions `build.yml`(macOS arm64·x86_64, Windows x86_64)이 테스트·웹 빌드·PyInstaller 번들·zip 을 만들고
Release 에 올린다. zip 을 풀어 더블클릭(`Trader.app` / `TraderApp.exe`)으로 메뉴바·트레이 아이콘·웹 UI 가 뜨는지는
사용자 실기 확인 항목이다(`docs/TODO.md` 「사용자 확인 대기」, 사용법 `docs/bundle-usage.md`).

## 5. 되돌리기 (언제든)

```
cd <새 저장소>
uv run trader install --uninstall
uv run trader install --uninstall --watchdog
cd <옛 저장소>
uv run trader install                   # 옛 데몬 로그온 등록 복구 (필요하면 --watchdog 도)
```

- 새 데몬이 떠 있으면 먼저 내린다(트레이 「종료」 또는 Ctrl+C — 실행 중 세션을 기다린 뒤 잠금 해제).
- 두 저장소의 원장(`state/trader.db`)은 별개다. 되돌린 뒤 새 저장소에서 난 주문·체결은 KIS 앱과 대조한다.
- 급히 주문만 멈추려면 새 저장소 `uv run trader accounts pause`(전 계좌 일시정지, 해제 `accounts resume`),
  옛 저장소는 `uv run trader control pause`.
