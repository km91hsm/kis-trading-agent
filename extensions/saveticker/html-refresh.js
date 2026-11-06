import { ownsApiTab } from './core.js';
export const HTML_INTERVAL_MS = 4 * 60000, HTML_TRIAL_MS = 35 * 60000;
export function beginHtmlTrial(now = Date.now()) {
    return { version: 1, status: 'active', startedAt: now, lastReloadAt: now, reloadCount: 0, pendingAt: null, pendingTabId: null };
}
export function htmlTrialTerminal(state, now = Date.now()) {
    if (!state.htmlRefreshTest) return null;
    const trial = state.htmlRefreshTrial;
    const terminal = (status, reason) => ({ ...trial, status, reason, endedAt: now });
    if (trial?.status !== 'active') return terminal('failed', 'invalid_trial');
    if (trial.version !== 1 || !Number.isFinite(now) || !Number.isFinite(trial.startedAt) || trial.startedAt < 0 || trial.startedAt > now || !Number.isFinite(trial.lastReloadAt) || trial.lastReloadAt < trial.startedAt || trial.lastReloadAt > now || !Number.isInteger(trial.reloadCount) || trial.reloadCount < 0 || trial.reloadCount > 8 || !(trial.pendingAt === null || Number.isFinite(trial.pendingAt) && trial.pendingAt >= trial.lastReloadAt && trial.pendingAt <= now)) return terminal('failed', 'invalid_trial');
    if (state.hostBlocked || state.blockedLanes?.length) return terminal('failed', 'verification_required');
    if (now - trial.startedAt >= HTML_TRIAL_MS) return terminal('finished', 'trial_complete');
    return null;
}
export async function htmlTrialStep({ chrome, state, token, active, store, halt, blockHost, busy = false, now = Date.now() }) {
    let trial = state.htmlRefreshTrial;
    if (!state.htmlRefreshTest || !active()) return false;
    const end = async (status, reason) => { if (active()) await halt({ ...trial, status, reason, endedAt: now }); return true; };
    const terminal = htmlTrialTerminal(state, now);
    if (terminal) { if (active()) await halt(terminal); return true; }
    if (busy || state.hostBlocked || state.blockedLanes?.length || Number.isFinite(state.apiCooldownUntil) && now < state.apiCooldownUntil) return true;
    const pending = trial.pendingAt !== null;
    if (pending && now - trial.pendingAt >= 30000) return end('failed', 'content_timeout');
    if (!pending && now - trial.lastReloadAt < HTML_INTERVAL_MS) return false;
    if (!pending && trial.reloadCount >= 8) return end('failed', 'reload_budget');
    if (!pending && (!Number.isFinite(state.lastApiHealthyAt) || state.lastApiHealthyAt < trial.startedAt || state.lastApiHealthyAt > now || now - state.lastApiHealthyAt > 120000)) return end('failed', 'health_stale');
    const tabs = (await chrome.tabs.query({ url: 'https://www.saveticker.com/*' })).filter(t => ownsApiTab(t, token));
    if (!active()) return true;
    if (tabs.length !== 1 || pending && tabs[0].id !== trial.pendingTabId) return end('failed', 'owned_tab_unverified');
    if (new URL(tabs[0].pendingUrl || tabs[0].url).search) { await blockHost(); return end('failed', 'verification_required'); }
    const tab = tabs[0];
    let response;
    try { response = await chrome.tabs.sendMessage(tab.id, { type: 'api_probe', token }); }
    catch { if (pending && now - trial.pendingAt < 30000) return true; return end('failed', 'content_unavailable'); }
    if (!active()) return true;
    if ([401, 403].includes(response?.status)) { await blockHost(); return end('failed', 'verification_required'); }
    if (response?.status !== 200 || response.ready !== true) {
        if (pending && now - trial.pendingAt < 30000) return true;
        return end('failed', 'page_unverified');
    }
    if (pending) {
        await store({ htmlRefreshTrial: { ...trial, pendingAt: null, pendingTabId: null }, connection: '정상 페이지 갱신 완료 · API 수집 재개' });
        return true;
    }
    // Reserve BEFORE navigation: worker restarts cannot repeat the same reload.
    const reserved = { ...trial, lastReloadAt: now, reloadCount: trial.reloadCount + 1, pendingAt: now, pendingTabId: tab.id };
    await store({ htmlRefreshTrial: reserved, connection: '정상 페이지 갱신 중 · API 대기' });
    trial = reserved;
    if (!active()) return true;
    try { await chrome.tabs.reload(tab.id); }
    catch { return end('failed', 'reload_failed'); }
    return true;
}
