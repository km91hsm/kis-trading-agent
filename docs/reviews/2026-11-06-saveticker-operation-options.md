# SaveTicker 화면 개입과 세션 신뢰성 조사

조사일: 2026-11-06. 공식 Chrome/Chromium·Cloudflare 문서와 이번 작업에서 전달받은 관찰 기록을 구분한다. 이 조사에서는 브라우저 조작, 실수집, 거래 실행을 하지 않았다. 아래 운영 구조는 제안이며 구현·배포 완료를 뜻하지 않는다.

**판단:** 단일 일반 Chrome 세션을 재사용하고, 별도 수집 서비스가 저장·상태·복구 한도를 관리하는 구성이 현재의 우선 후보다. 최소화는 화면 개입을 줄이지만 무중단 실행을 보장하지 않는다. 4분 HTML 갱신의 효과와 35분 잠금 수집 성공은 아직 입증되지 않았다.

## 1. 현재 확인된 동작과 시험의 한계

현재 소스는 MV3 확장 서비스 워커가 Native Messaging을 연결하고, 소유한 일반 Chrome 탭에 API 작업을 전달한다. 새 전용 창은 `focused: false`로 만들며 정상 수집이 확인되면 최소화를 유지한다. API 모드는 최소화된 창도 허용한다. 확장 팝업, 전용 Chrome 창, 서비스 워커, Native host는 서로 다른 수명과 역할을 가진다. [worker.js](../../extensions/saveticker/worker.js), [api-runner.js](../../extensions/saveticker/api-runner.js), [minimized.js](../../extensions/saveticker/minimized.js)

| 관찰 조건 | 관찰 결과 | 입증 범위 |
|---|---|---|
| 재로그인 후 전용 탭 정상 새로고침 | 약 12분 저장 후 재확인 상태 재발 | 재로그인·새로고침만으로 장기 안정화되지 않음 |
| 기존 collector의 headless 단발, 새 세션 | `login_profile` 단계 HTTP 403 | 이 조합 실패. headless 여부와 세션 신규 여부가 함께 바뀌어 원인을 분리할 수 없음 |
| 쿠키 초기화 후 정상 HTML 갱신 시험 | 19:12 KST 저장 시작. 설정은 4분 간격, 최대 8회, 총 35분 | 설정한 갱신 횟수와 실제 수행·완료 횟수는 별개 |
| 같은 시험의 macOS 잠금 | 19:24:13 `IOConsoleLocked=True` 최초 확인, 19:26:13까지 모니터에서 `collecting` 관찰 | 잠금 직후 모든 수집이 즉시 중단된다는 가설과 맞지 않음 |
| 마지막 상태 | 19:26:12.260 마지막 유효 저장, Native runtime `updated_at=19:26:19.576`, `runner=paused`, `reason=extension_disconnected` | **35분 시험 미완료.** 19:40 팝업에서 정상 갱신 3회와 서버 보안 확인 응답 감지, 전체 사용자 확인 대기를 확인. macOS 잠금이 원인인지는 미확인 |

앞선 관찰은 [리플랫폼 검증 기록](2026-11-06-saveticker-replatform.md)에 있으며 19:24 이후 시각·상태는 주 작업자의 모니터 및 후속 UI 확인 기록을 사용했다. 이 조사자가 런타임 파일을 다시 읽어 독립 검증한 값은 아니다. 일부 저장 성공은 모든 스트림·본문·PDF 완전성을 의미하지 않는다.

## 2. 공식 문서가 설명하는 서로 다른 중단 계층

| 계층 | 공식 근거 | 이번 상황에 대한 해석·한계 |
|---|---|---|
| 확장 action 팝업 | 다른 영역에 포커스를 두면 자동으로 닫힌다. [Chrome 팝업](https://developer.chrome.com/docs/extensions/develop/ui/add-popup) | 팝업은 제어 화면으로 두고 수집 상태는 외부에 보존해야 한다. 팝업 닫힘과 수집 전용 창 닫힘을 같은 사건으로 기록하면 안 된다. |
| 최소화·숨김 페이지의 JS 타이머 | 일반적으로 최소화·비활성 탭은 hidden이다. Chrome 88 문서는 조건에 따라 타이머를 초 단위로 묶고, hidden 5분 초과·연쇄 5회 이상·30초 이상 무음·WebRTC 미사용이 겹치면 분 단위 점검을 설명한다. [Chrome timer throttling](https://developer.chrome.com/blog/timer-throttling-in-chrome-88) | 페이지의 `setTimeout`/`setInterval` 목표 시각을 보장하지 않는다. 이 규칙만으로 Native 메시지나 모든 fetch가 1분에 한 번이라고 결론내릴 수 없다. 설치 Chrome에서 실제 지연은 측정해야 한다. |
| 페이지 frozen / discarded | frozen에서는 JS 타이머·fetch 콜백 같은 freezable task가 중단된다. discarded에서는 JS를 실행할 수 없으며 복귀 시 새로고침이 필요하다. `freeze`, `resume`, `document.wasDiscarded`로 일부 전이를 관찰할 수 있다. [Chrome Page Lifecycle](https://developer.chrome.com/docs/web-platform/page-lifecycle-api) | hidden과 frozen은 같은 상태가 아니다. API 요청을 페이지 문맥에서 실행하므로 DOM 읽기를 줄여도 페이지 수명 의존성은 남는다. 현재 시험에서 frozen/discarded 발생 여부는 미확인이다. |
| macOS 창 가림과 렌더러 | Chromium의 Mac 설계 문서는 화면에 보이는 NSView 상태와 렌더러의 hidden 상태를 구분하며, 렌더러 hidden은 프레임 생산 중단·리소스 해제와 관련된다고 설명한다. [Mac Window Occlusion 설계](https://www.chromium.org/developers/design-documents/mac-occlusion/) | 과거 설계 문서이므로 현재 버전의 잠금 동작을 확정하는 자료로 쓰지 않는다. 렌더링 최적화와 타이머 제한, 사이트 인증 만료를 구분하는 근거다. |
| OS 화면 잠금 | `chrome.idle`은 `active`, `idle`, `locked`를 별도 상태로 제공한다. 이벤트의 locked는 화면 잠금 또는 화면 보호기 활성화에 대응한다. [Chrome idle API](https://developer.chrome.com/docs/extensions/reference/api/idle) | 최소화, OS 잠금, 화면 전원 꺼짐, 시스템 잠자기를 따로 기록해야 한다. 잠금 상태가 곧 탭 frozen 또는 사이트 차단이라는 공식 보장은 확인하지 못했다. |
| MV3 서비스 워커 | 일반적으로 비활동 30초 등 종료 조건이 있다. Chrome 105부터 `connectNative()` 연결은 워커를 유지하지만 host 종료로 포트가 닫히면 워커도 타이머 후 종료될 수 있다. 전역 변수 대신 저장소에 상태를 보존하도록 권고한다. [서비스 워커 수명](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle) | Native 연결 유지의 대상은 확장 워커다. 페이지 실행·인증·OS 실행 상태까지 보장하지 않는다. `extension_disconnected` 하나로 어느 쪽이 먼저 종료됐는지 알 수 없다. |
| Native host 프로세스 | `connectNative()`는 포트가 유지되는 동안 host 프로세스를 유지한다. `sendNativeMessage()`는 메시지마다 새 host를 시작한다. [Native Messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging) | Chrome이 시작한 host 자체를 브라우저와 독립된 상시 감독 서비스로 간주하면 안 된다. 별도 서비스와 통신하는 연결 계층으로 분리할 수 있다. |
| 확장 알람과 시스템 잠자기 | `chrome.alarms`는 예정 시각보다 지연될 수 있고, 장치를 깨우지 않는다. 깨어난 뒤 놓친 반복 알람은 최대 한 번 발생한다. 시작 시 필요한 알람 존재를 확인하는 방식이 권고된다. [Chrome alarms](https://developer.chrome.com/docs/extensions/reference/api/alarms) | 알람은 복구 트리거 후보이지 정밀한 수집 주기나 잠자기 중 수집 보장이 아니다. 설치 버전에 맞춰 재시작 시 알람을 복원해야 한다. |

**추론:** 화면에 창을 반복 표시하는 문제와 세션 재확인 문제를 하나의 headless 전환으로 해결하기 어렵다. 최소화된 일반 탭은 화면 개입을 줄일 수 있으나, 별도로 페이지 생존·Native 연결·인증 상태를 감시해야 한다. Chromium 테스트 도구도 자동화가 타이머·창 가림 관련 기본 플래그를 바꿀 수 있음을 명시하므로 자동화 브라우저 시험 결과를 일반 Chrome과 바로 동일시하지 않는다. [Chromium Crossbench 변경 기록](https://chromium.googlesource.com/crossbench/+/4b4a493a965b9e6065520f67a4249362973bbcb3)

## 3. Cloudflare JSD: 15분은 원인 확정이나 복구 보장이 아니다

**공식 사실:** JavaScript Detections는 HTML 페이지 응답에 삽입되고 AJAX에는 삽입되지 않는다. 수명은 15분이며 문서는 만료 전 코드 재삽입을 설명한다. 성공 결과가 있어도 다른 탐지 신호가 최종 판정에 영향을 줄 수 있다. 또한 JSD는 Challenge Pages·Turnstile과 별개다. [Cloudflare JavaScript Detections](https://developers.cloudflare.com/cloudflare-challenges/challenge-types/javascript-detections/)

**추론:** API fetch만 이어지는 동안 정상 HTML 방문이 부족한지 시험할 가치는 있다. 그러나 SaveTicker가 어떤 Cloudflare 기능·WAF 규칙을 적용하는지는 확인되지 않았다. 약 12분 후 재확인과 문서의 15분 수명은 원인을 입증하지 않으며, SaveTicker 로그인 수명이나 모든 `cf_clearance`의 수명을 15분으로 단정해서도 안 된다.

**권고:** 4분 정상 HTML 갱신은 한정된 비교 실험으로만 취급한다. 새로고침 시도, HTML 로드 완료, 이후 유효 저장을 각각 확인해야 한다. 요청이 발생했다는 이유로 JSD 성공이나 인증 연장을 기록하지 않는다. 재확인이 나타나면 반복 새로고침 대신 명시적인 인증 대기 상태로 전환한다.

## 4. SaveTicker 공식 API·내보내기 확인 범위

공개 검색 네 건을 수행했다: `SaveTicker 세이브티커 공식 API 내보내기`, `site:saveticker.com API export 내보내기`, `site:saveticker.com (API OR "내보내기" OR export OR 개발자)`, `"세이브티커" "API" "공식"`. 공식 API 계약·인증 방식·이용 한도 문서나 전체 데이터 내보내기 안내는 **미확인**이다. 제공하지 않는다는 결론은 아니다. 검색에서 기사에 등장한 API 용어와 제3자 설계 문서는 제품 기능의 증거로 채택하지 않았다.

현재 코드의 `/api/...`는 웹앱 내부 요청 계약이며, 외부 수집용 공식 API 제공을 뜻하지 않는다. 기존 로컬 `trader saveticker export`도 로컬 저장 결과 재생성 기능으로서 SaveTicker 공식 내보내기와 구분해야 한다. [API 계약 코드](../../src/trader/features/saveticker/api_contract.py), [기존 수집 문서](../saveticker-collection.md)

운영자가 공식 API·내보내기 제공 범위와 자동 이용 조건을 확인할 수 있다면 해당 문서를 근거로 어댑터를 다시 평가한다. 그 전에는 공식 API가 있다는 전제로 설계하지 않는다.

## 5. 권장 운영 구조와 남은 검증

| 구성 | 맡을 일 | 보장하지 않는 것 |
|---|---|---|
| 단일 persistent user Chrome 세션 | 같은 사용자 프로필·소유 탭을 재사용. 정상 수집 중 최소화·포커스 유지. 정상 HTML 탐색과 허용된 페이지 문맥 요청 수행 | 인증 무기한 유지, Chrome 종료 후 수집, 백그라운드 탭 무중단 |
| 별도 collector service | OS가 관리하는 단일 실행, 수집 일정·기존 정규화·저장 계약·신선도 감시·수동 중지 관리 | 브라우저 인증 대체, 시스템 잠자기 중 연속 수집 |
| MV3 ↔ Native 연결 계층 | 허용된 메시지 전달, heartbeat와 재연결. 포트 재연결과 사이트 요청 재시도를 별도 예산으로 관리 | Native 연결 자체가 사이트 세션을 복구한다는 가정 |
| persisted recovery state | 장애 발생 시각·단계·고정 원인 코드·시도 횟수·다음 가능 시각·수동 중지·마지막 유효 저장을 보존. 재시작해도 제한과 인증 대기 유지 | 서비스 재시작으로 차단·재시도 예산 초기화 |
| bounded manual auth handoff | 인증 필요 시 수집 중지, 알림 한 번, 사용자가 해제한 화면에서 같은 소유 탭으로 정상 인증. 완료 후 허용 응답·저장을 확인하고 재개 | 인증 UI 자동 통과, 자동 쿠키 초기화 반복, 반복 전면 창 표시 |

권장 복구 전이는 `collecting → reconnect_wait / network_backoff / auth_required / paused`처럼 원인별로 나눈다. 연결 끊김은 제한된 재연결로, 요청 제한은 서버 대기 시간으로, 인증 필요는 수동 인계로 처리한다. 이 상태 이름과 구조는 제안이며 현재 구현 상태가 아니다.

검증은 다음을 구분하여 수행한다.

1. **19:26 중단 원인 확인:** 19:40 팝업에서 서버 보안 확인 응답 감지와 정상 갱신 3회를 확인했다. Native의 일반 연결 종료 사유만으로 판정하지 않았으며, 잠금과 재확인의 인과는 여전히 미확인이다.
2. **화면 개입 감소:** 정상 최소화 운용에서 새 창 생성·포커스 이동 횟수를 기록한다. 수동 인증은 별도 횟수로 센다.
3. **세션 지속성:** Chrome/macOS 버전·화면 상태·네트워크·갱신 정책을 고정하고, 정상 HTML 갱신 여부만 바꿔 비교한다. 실제 갱신 횟수·지연·저장 공백·재확인을 기록한다.
4. **잠금 시험:** `IOConsoleLocked=True` 확인 시점부터 잠금 구간을 측정한다. 35분 시험 전체, 잠금 실제 지속 시간, 스트림별 신선도, 본문·PDF 실패를 각각 판정한다. 현재 약 2분의 잠금 중 저장을 35분 성공으로 확대하지 않는다.
5. **복구 상태 내구성:** 브라우저/워커/host/서비스 재시작 때 중복 작업·중복 창·예산 초기화가 없고, 이전 유효 데이터와 중지 사유가 보존되는지 확인한다. 페이지 lifecycle 사건이 없는 연결 끊김도 별도로 다룬다.

현재 도달한 결론은 “최소화 상태의 짧은 수집과 잠금 직후 일부 저장은 관찰했으나, 서버 보안 확인으로 중지되었고 장시간 지속성은 미확인”이다.
