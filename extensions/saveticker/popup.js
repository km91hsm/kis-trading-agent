const $ = id => document.getElementById(id);
const extensionVersion = chrome.runtime.getManifest().version;
const errors = { api_contract_unverified: '이 항목의 API 요청 조건이 아직 확인되지 않았습니다', api_request_failed: 'API 응답 또는 저장을 확인하지 못했습니다', window_minimized: '전용 Chrome 창을 복원한 뒤 기다려 주세요', tab_activation_failed: '전용 탭을 활성화하지 못했습니다', selection_unverified: '화면의 선택 상태를 확인하지 못했습니다', begin_failed: '수집 작업을 시작하지 못했습니다', lane_busy: '이전 수집 작업의 종료를 기다립니다', state_failed: '호스트에 진행 상태를 전달하지 못했습니다', navigation_failed: '전용 탭을 열지 못했습니다', content_unavailable: '전용 탭의 화면에 연결하지 못했습니다', snapshot_failed: '화면의 선택 상태 또는 저장 요청을 확인하지 못했습니다', postprocess_failed: '수집 후 처리를 마치지 못했습니다', job_release_failed: '실패한 수집 작업을 정리하지 못했습니다', native_tick_failed: '호스트 연결을 확인해 주세요' };
const pdfStates = { api_fetching: 'PDF 응답 확인 중', api_failed: 'PDF 수집 또는 저장 응답 미확인', button_unverified: '일치하는 PDF 버튼 하나를 확인하지 못했습니다', candidate_missing: 'PDF 버튼에서 파일 주소가 전달되지 않았습니다', handoff_aborted: 'PDF 연결이 취소되었습니다', waiting_hook: 'PDF 연결 준비 중', hook_not_ready: 'PDF 연결 도우미가 준비되지 않았습니다', url_unverified: 'PDF 주소를 확인하지 못했습니다', starting: 'PDF 다운로드 시작 중', downloading: 'PDF 다운로드 중', saved: 'PDF 저장 완료', download_start_failed: 'PDF 다운로드를 시작하지 못했습니다', download_interrupted: 'PDF 다운로드가 중단되었습니다', native_failed: 'PDF 저장 검증을 통과하지 못했습니다', filename_unverified: 'PDF 파일명을 확인하지 못했습니다', pdf_begin_busy: 'PDF 발급 대기: 이전 요청의 유효 시간이 남아 있습니다', pdf_begin_unknown_report: 'PDF 발급 실패: 현재 리포트를 확인하지 못했습니다', pdf_begin_cached: 'PDF 발급 생략: 이미 저장된 파일입니다', pdf_begin_failed: 'PDF 발급 단계에서 실패했습니다', pdf_save_ticket: 'PDF 저장 실패: 발급된 다운로드 요청을 확인하지 못했습니다', pdf_save_path: 'PDF 저장 실패: 발급된 파일 경로를 확인하지 못했습니다', pdf_save_invalid: 'PDF 저장 실패: 유효한 PDF 파일이 아닙니다', pdf_save_failed: 'PDF 저장 검증 단계에서 실패했습니다' };
const laneNames = { news: '뉴스', community: '커뮤니티', market: '증시', background: '캘린더·리포트' };
Object.assign(errors, { invalid_pdf_path: 'PDF 저장 경로를 확인하지 못했습니다', invalid_pdf_ticket: 'PDF 저장 요청이 만료됐거나 일치하지 않습니다', invalid_pdf: '유효한 PDF 파일이 아닙니다', api_response_unverified: '응답 형식·상태를 확인하지 못했습니다', api_shape_unverified: '데이터 구조를 확인하지 못했습니다', api_context_unverified: '기본 그룹을 확인하지 못했습니다', api_detail_unverified: '본문 응답을 확인하지 못했습니다', api_source_unverified: '출처를 확인하지 못했습니다', api_basis_unverified: '실시간·정규장 기준을 확인하지 못했습니다', api_regular_unverified: '정규장 기준을 확인하지 못했습니다', api_pdf_unverified: 'PDF 응답을 확인하지 못했습니다', api_response_too_large: '응답 크기 제한을 넘었습니다', api_busy: '이전 응답 처리를 기다립니다', api_content_unavailable: '전용 탭 응답이 없습니다', api_already_prepared: '이미 준비된 작업입니다', invalid_job: '작업이 만료됐거나 일치하지 않습니다', invalid_message: '호스트 메시지를 확인하지 못했습니다', snapshot_too_large: '저장 크기 제한을 넘었습니다', unknown_stream: '수집 항목을 확인하지 못했습니다' });
const stages = { begin: '작업 발급', fetch: '응답 수신', prepare: '본문 준비', detail: '본문 수신', snapshot: '데이터 저장', pdf: 'PDF 처리', download: '파일 다운로드', save: '파일 저장' };
errors.filename_unverified = '지정한 PDF 파일명과 실제 다운로드 파일명이 다릅니다';
async function render() {
    const s = await chrome.storage.local.get(null);
    const knownVersion = typeof s.workerVersion === 'string' && /^\d+\.\d+\.\d+$/.test(s.workerVersion);
    const workerVersion = knownVersion ? s.workerVersion : s.enabled ? '연결 중' : '시작 전';
    $('version').textContent = '확장 ' + extensionVersion + ' · 수집기 ' + workerVersion + (knownVersion && workerVersion !== extensionVersion ? ' · 확장 새로고침 필요' : '');
    $('pdf-state').textContent = Object.hasOwn(pdfStates, s.pdfState) ? pdfStates[s.pdfState] + ' (' + s.pdfState + ')' : '';
    $('api-mode').checked = s.backend === 'api';
    $('api-mode').disabled = !!s.enabled;
    $('api-coverage').textContent = s.backend === 'api' ? ('확인된 API ' + (s.apiSupportedCount || 0) + '/' + (s.planCount || 20) + '항목 · 실시간·정규장 서버 기준 확인') : '';
    $('html-refresh').checked = s.htmlRefreshTest === true;
    $('html-refresh').disabled = !!s.enabled || s.backend !== 'api' || !!s.autoRecheck;
    $('auto-recheck').checked = !!s.autoRecheck;
    $('auto-recheck').disabled = !!s.enabled || s.backend !== 'api' || !!s.htmlRefreshTest;
    const recoveryReasons = { recovery_used: '이미 1회 복구함', healthy_period_unverified: '정상 수집 10분 미확인 또는 연속 실패', recovery_timeout: '45초 복구 시간 초과', invalid_recovery: '복구 상태 미확인', owner_unverified: '전용 탭 미확인', page_unverified: '로그인·정상 페이지 미확인', content_unavailable: '페이지 응답 미확인', not_app_recheck: '안내창 감지 전 접근 거부', reload_failed: '새로고침 실패', credentials_missing: '로그인 정보 없음 · trader saveticker login set', login_failed: '이메일 로그인 실패', login_form_missing: '로그인 화면 미확인' };
    const recovery = s.recheckRecovery;
    $('recheck-status').textContent = recovery?.observedAt != null && recovery.attempted === false ? '안내창 표시 대기 · 최대 15초' : recovery?.pendingAt != null ? '일반 페이지 복구 대기 · 최대 45초' : recovery?.attempted ? (recovery.reason === 'recovered' ? (s.repeatRecheck ? '복구 완료 · 정상 수집 10분 뒤 새 사건 허용' : '1회 복구 완료 · 재발 시 중지') : '자동 복구 중지 · ' + (recoveryReasons[recovery.reason] || '직접 확인 필요') + (Number.isInteger(recovery.tabId) ? ' · 새로고침 예약됨' : ' · 새로고침 안 함')) : s.repeatRecheck ? '사건당 1회 · 정상 수집 10분 뒤에만 다음 복구' : '한 실행에서 1회만 시도 · 로그인 또는 재실패 시 중지';
    $('repeat-recheck').checked = !!s.repeatRecheck;
    $('repeat-recheck').disabled = !!s.enabled || s.backend !== 'api' || !s.autoRecheck || !!s.persistRecheck;
    $('persist-recheck').checked = !!s.persistRecheck;
    $('persist-recheck').disabled = !!s.enabled || s.backend !== 'api' || !s.autoRecheck;
    if (s.persistRecheck && recovery?.persist === true) {
        const two = n => String(n).padStart(2, '0'), next = Number.isFinite(recovery.nextAt) ? new Date(recovery.nextAt) : null;
        const failures = Number.isSafeInteger(recovery.failures) ? recovery.failures : 0;
        $('recheck-status').textContent = recovery.pendingAt != null ? '새로고침 뒤 페이지 확인 중 · 최대 45초'
            : recovery.reason === 'recovered' ? '복구 완료 · 수집 재개'
            : Object.hasOwn(recoveryReasons, recovery.reason) && !s.enabled ? '자동 복구 중지 · ' + recoveryReasons[recovery.reason]
            : '계속 복구 중' + (Object.hasOwn(recoveryReasons, recovery.reason) ? ' · ' + recoveryReasons[recovery.reason] : '') + (next && next.getTime() > Date.now() ? ' · 다음 시도 ' + two(next.getHours()) + ':' + two(next.getMinutes()) : '');
        $('recheck-status').textContent += ' · 연속 시도 ' + failures + '회';
    } else if (s.persistRecheck && !recovery) $('recheck-status').textContent = '재확인 시 멈추지 않고 1→2→4→8→15분 간격으로 새로고침·로그인';
    $('keep-minimized').checked = !!s.keepMinimized;
    $('keep-minimized').disabled = !!s.enabled || s.backend !== 'api';
    $('window-status').textContent = s.keepMinimized ? (s.ownedWindowState === 'minimized' ? '전용 창 최소화 확인됨 · 화면 확인은 수집 중지 후' : s.ownedWindowState === 'unverified' ? '전용 창 최소화 확인 필요' : '정상 수집 확인 후 전용 창 최소화') : '화면 확인은 수집을 중지한 뒤 진행하세요.';
    const trial = s.htmlRefreshTrial;
    const trialNames = { active: s.enabled ? '시험 진행 중' : '시험 중지', finished: '35분 시험 완료', failed: '시험 중지 · 직접 확인 필요' };
    $('html-trial').textContent = trial && Object.hasOwn(trialNames, trial.status) ? trialNames[trial.status] + ' · 정상 갱신 ' + (Number.isInteger(trial.reloadCount) && trial.reloadCount >= 0 && trial.reloadCount <= 8 ? trial.reloadCount : 0) + '/8회' : '선택 시 정상 상태에서만 4분마다 갱신 · 보안 확인 시 중지';
    if (s.repeatRecheck && recovery?.reason === 'recovered') {
        const n = s.hostBlocked ? s.blockedAt : Date.now();
        const fresh = Number.isFinite(n) && Number.isFinite(s.apiHealthySince) && Number.isFinite(s.lastApiHealthyAt) && s.apiHealthySince > 0 && s.apiHealthySince <= s.lastApiHealthyAt && s.lastApiHealthyAt <= n && n - s.lastApiHealthyAt <= 120000;
        const minutes = fresh ? Math.min(10, Math.floor((s.lastApiHealthyAt - s.apiHealthySince) / 60000)) : 0;
        $('recheck-status').textContent += ' · 정상 저장 ' + minutes + '/10분';
    }
    if (Number.isSafeInteger(recovery?.reloadCount) && recovery.reloadCount > 0) $('recheck-status').textContent += ' · 새로고침 시도 ' + recovery.reloadCount + '회';
    if (s.blockedCause === 'cloudflare_challenge') $('recheck-status').textContent += ' · 서버 보안 확인 응답 감지';
    $('connection').textContent = s.connection || (s.enabled ? '연결 대기' : '중지됨');
    for (const lane of ['news', 'community', 'market', 'background'])
        $(lane).textContent = s['lane_' + lane] || '대기';
    $('diagnostics').replaceChildren();
    for (const [lane, name] of Object.entries(laneNames)) {
        for (const key of ['lane_error_' + lane, 'lane_release_error_' + lane]) {
            const code = s[key];
            if (errors[code]) {
                const li = document.createElement('li');
                const stage = s.backend === 'api' && key === 'lane_error_' + lane ? stages[s['lane_error_stage_' + lane]] : null;
                li.textContent = name + (typeof stage === 'string' ? ' · ' + stage : '') + ': ' + errors[code] + ' (' + code + ')';
                $('diagnostics').append(li);
            }
        }
    }
    if (s.pdfState === 'api_failed' || s.pdfState === 'filename_unverified') {
        const li = document.createElement('li'), code = s.pdfState === 'filename_unverified' ? 'filename_unverified' : Object.hasOwn(errors, s.pdfError) ? s.pdfError : 'api_request_failed';
        const stage = stages[s.pdfState === 'filename_unverified' ? 'download' : s.pdfErrorStage];
        li.textContent = 'PDF' + (typeof stage === 'string' ? ' · ' + stage : '') + ': ' + errors[code] + ' (' + code + ')';
        $('diagnostics').append(li);
    }
    if (!$('diagnostics').children.length) {
        const li = document.createElement('li');
        li.textContent = '현재 기록된 수집 오류가 없습니다.';
        $('diagnostics').append(li);
    }
    $('updated').textContent = s.lastCollected ? '최근 수집 ' + new Date(s.lastCollected).toLocaleString('ko-KR') : '아직 수집 기록이 없습니다.';
    $('start').disabled = !!s.enabled;
    $('stop').disabled = !s.enabled;
    $('resume').disabled = false;
}
for (const action of ['start', 'stop', 'resume'])
    $(action).addEventListener('click', async () => {
        for (const b of document.querySelectorAll('button'))
            b.disabled = true;
        try {
            await chrome.runtime.sendMessage({ action });
        }
        finally {
            await render();
        }
    });
chrome.storage.onChanged.addListener(render);
void render();

$('api-mode').addEventListener('change', async () => { await chrome.runtime.sendMessage({ action: 'set_backend', backend: $('api-mode').checked ? 'api' : 'dom' }); await render(); });

$('html-refresh').addEventListener('change', async () => { try { await chrome.runtime.sendMessage({ action: 'set_html_refresh_test', value: $('html-refresh').checked }); } finally { await render(); } });

$('auto-recheck').addEventListener('change', async () => { try { await chrome.runtime.sendMessage({ action: 'set_auto_recheck', value: $('auto-recheck').checked }); } finally { await render(); } });

for (const [id, action] of [['repeat-recheck', 'set_repeat_recheck'], ['persist-recheck', 'set_persist_recheck'], ['keep-minimized', 'set_keep_minimized']]) $(id).addEventListener('change', async () => { try { await chrome.runtime.sendMessage({ action, value: $(id).checked }); } finally { await render(); } });
