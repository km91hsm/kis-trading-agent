import { recordHealth } from './recovery-health.js';
import { LANES, ownsApiTab as owned } from './core.js';
const ROOT = 'https://www.saveticker.com/news';
const API_ERRORS = new Set('api_contract_unverified api_response_unverified api_shape_unverified api_context_unverified api_detail_unverified api_source_unverified api_basis_unverified api_regular_unverified api_pdf_unverified api_response_too_large api_busy api_content_unavailable api_already_prepared invalid_pdf_path invalid_pdf_ticket invalid_pdf invalid_job invalid_message lane_busy snapshot_too_large unknown_stream'.split(' '));
export function apiErrorCode(error) {
    return API_ERRORS.has(error?.message) ? error.message : 'api_request_failed';
}
export function retryDeadline(value, now = Date.now()) {
    let delay = typeof value === 'string' && /^\d+$/.test(value.trim()) ? Number(value.trim()) * 1000 : Date.parse(value) - now;
    if (!Number.isFinite(delay) || delay < 0 || delay > 31536000000) delay = 60000;
    return now + Math.max(60000, delay);
}
export async function abortApi(chrome, token) {
    const tabs = await chrome.tabs.query({ url: 'https://www.saveticker.com/*' });
    for (const tab of tabs.filter(t => owned(t, token)))
        try { await chrome.tabs.sendMessage(tab.id, { type: 'api_abort', token }); } catch { }
}
export async function apiCycle({ chrome, token, scheduler, active, request, store, blockHost, health = {}, setupRequired = false, onSetup = async () => {}, onPdf = async () => {}, wait = ms => new Promise(resolve => setTimeout(resolve, ms)) }) {
    if (!active()) return;
    const candidates = await chrome.tabs.query({ url: 'https://www.saveticker.com/*' });
    if (!active()) return;
    let tab = candidates.find(t => owned(t, token));
    if (!tab) {
        // A single owned tab, no focus changes. Minimized windows are valid.
        const window = await chrome.windows.create({ url: ROOT + '#trader-saveticker-api=' + token, focused: false });
        if (!active()) return;
        tab = window.tabs?.[0];
        await store({ apiWindowId: window.id, connection: 'API 전용 탭 준비 중 · 다음 주기 대기' });
        if (!tab) return;
        // Do not send a message or navigate repeatedly while the page loads.
        return;
    }
    const fetchJob = async job => {
        await wait(2000);
        if (!active()) return null;
        return chrome.tabs.sendMessage(tab.id, { type: 'api_fetch', token, job });
    };
    const blocked = async response => {
        if (response?.status === 401 || response?.status === 403) { await blockHost(response); return true; }
        if (response?.status === 429) { await store({ apiCooldownUntil: retryDeadline(response.retry_after), connection: '요청 제한 · 서버 대기 시간 적용' }); return true; }
        return false;
    };
    if (setupRequired) {
        try {
            const job = await request('api_context_begin');
            if (!active()) return;
            const response = await fetchJob(job);
            if (!active() || await blocked(response)) return;
            if (!response || response.error) throw Error('api_context_unverified');
            await request('api_context', { job_id: job.job_id, response });
            if (active()) { await store({ apiSetupRetryAt: 0 }); await onSetup(); }
        } catch { if (active()) await store({ apiSetupRetryAt: Date.now() + 60000, connection: 'API 기본 그룹 확인 실패 · 다음 주기 대기' }); }
        return;
    }
    for (const lane of LANES) {
        if (!active()) break;
        const view = scheduler.next(Date.now(), lane);
        if (!view) continue;
        let job, saved = false, failed = true, authBlocked = false, stage = 'begin';
        try {
            job = await request('api_begin', { stream: view.stream });
            if (!active()) return;
            stage = 'fetch';
            const response = await fetchJob(job);
            if (!active()) return;
            if (response?.status === 401 || response?.status === 403) {
                authBlocked = true;
                await blockHost(response);
                return;
            }
            if (response?.status === 429) {
                // Persist before job cleanup so stop/disconnect cannot lose it.
                await store({ apiCooldownUntil: retryDeadline(response.retry_after), connection: '요청 제한 · 서버 대기 시간 적용' });
                return;
            }
            if (!response || response.error) throw Error(response?.error || 'api_content_unavailable');
            const details = [];
            if (view.stream === 'news_all' || view.stream === 'news_reports') {
                stage = 'prepare';
                const prepared = await request('api_prepare', { stream: view.stream, job_id: job.job_id, response });
                if (!active()) return;
                for (const child of (prepared.requests || []).slice(0, 2)) {
                    stage = 'detail';
                    const detail = await fetchJob(child);
                    if (!active()) return;
                    if (await blocked(detail)) { authBlocked = detail?.status !== 429; return; }
                    if (!detail || detail.error) throw Error(detail?.error || 'api_detail_unverified');
                    details.push(detail);
                }
            }
            stage = 'snapshot';
            const result = await request('api_snapshot', { stream: view.stream, job_id: job.job_id, response, details });
            saved = true;
            if (!active()) return;
            failed = false;
            for (const item of (result.pdf_items || []).slice(0, 1)) {
                stage = 'pdf';
                if (!active()) return;
                const result = await onPdf(item, tab);
                if (!active()) return;
                if (result && await blocked(result)) return;
            }
            Object.assign(health, recordHealth(health, Date.now()));
            await store({ connection: '연결됨 · API 수집 중', ...health, ['lane_' + lane]: 'API 부분 수집 · ' + (result.count || 0) + '개', ['lane_error_' + lane]: null, ['lane_error_stage_' + lane]: null, ...(result.count > 0 ? { lastCollected: Date.now() } : {}), apiCoverage: 'API 부분 수집 · 서버 응답 기준 검증' });
        } catch (error) {
            failed = true;
            if (!active()) return;
            const unsupported = error?.message === 'api_contract_unverified';
            await store({ ['lane_' + lane]: unsupported ? 'API 요청 조건 미확인 · 대기' : 'API 수집 오류 · 다음 주기 대기', ['lane_error_' + lane]: apiErrorCode(error), ['lane_error_stage_' + lane]: stage });
        } finally {
            // Cancel only this exact job. Errors do not replace the last good capture.
            if (job && !saved && active()) try { await request('api_cancel', { stream: view.stream, job_id: job.job_id, ...(authBlocked ? { reason: 'verification_required' } : {}) }); } catch { }
            if (active()) {
                scheduler.done(view, Date.now(), failed);
                await store({ collectionSchedule: scheduler.snapshot() });
            }
        }
    }
}
