import { repeatEligible } from './recovery-health.js';
import { ownsApiTab } from './core.js';
export async function recoverRecheck({ chrome, state, token, active, store, stop, resume, report = async () => {}, busy = false, now = Date.now(), clock }) {
    if (!active() || !state.autoRecheck || state.backend !== 'api' || !state.hostBlocked) return false;
    const began = performance.now();
    clock ||= () => now + Math.max(0, performance.now() - began);
    let old = state.recheckRecovery;
    const halt = async reason => {
        if (active()) {
            await store({ recheckRecovery: { ...old, attempted: true, pendingAt: null, reason }, connection: '재확인 자동 복구 중지 · 직접 확인 필요' });
            if (active()) {
                try { await report({ category: 'recheck', reason, reload_count: old?.reloadCount ?? 0 }); }
                catch { /* 진단 전송 실패도 수집 중지를 막지 않는다. */ }
            }
            if (active()) await stop();
        }
        return true;
    };
    const pending = old?.pendingAt != null;
    const observing = old?.attempted === false && Number.isFinite(old.observedAt) && old.observedAt <= now;
    if (observing && now - old.observedAt >= 15000) return halt('not_app_recheck');
    if (old && (!old.attempted && !observing || pending && (!Number.isFinite(old.pendingAt) || old.pendingAt > now || !Number.isInteger(old.tabId)))) return halt('invalid_recovery');
    if (old?.attempted && !pending && !repeatEligible(state, old, now)) return halt(state.repeatRecheck ? 'healthy_period_unverified' : 'recovery_used');
    if (pending && now - old.pendingAt >= 45000) return halt('recovery_timeout');
    if (busy || Number.isFinite(state.apiCooldownUntil) && now < state.apiCooldownUntil) return true;
    const tabs = (await chrome.tabs.query({ url: 'https://www.saveticker.com/*' })).filter(t => ownsApiTab(t, token));
    if (!active()) return true;
    if (tabs.length !== 1 || pending && tabs[0].id !== old.tabId) return halt('owner_unverified');
    const tab = tabs[0];
    if (new URL(tab.pendingUrl || tab.url).search) return pending ? true : halt('page_unverified');
    let response, timer;
    const remaining = pending ? old.pendingAt + 45000 - clock() : observing ? old.observedAt + 15000 - clock() : 10000;
    if (remaining <= 0) return halt('recovery_timeout');
    const timeout = Symbol('timeout');
    try {
        response = await Promise.race([
            chrome.tabs.sendMessage(tab.id, { type: 'api_probe', token }),
            new Promise(resolve => { timer = setTimeout(() => resolve(timeout), Math.min(10000, remaining)); })
        ]);
    } catch { return pending && clock() < old.pendingAt + 45000 ? true : halt('content_unavailable'); }
    finally { clearTimeout(timer); }
    if (pending && (clock() >= old.pendingAt + 45000 || response === timeout && remaining <= 10000)) return halt('recovery_timeout');
    if (observing && (clock() >= old.observedAt + 15000 || response === timeout && remaining <= 10000)) return halt('not_app_recheck');
    if (response === timeout) return pending ? true : halt('content_unavailable');
    if (!active()) return true;
    if (pending) {
        // Let Chrome load the ordinary page itself; no challenge interaction.
        if (response?.page_state === 'challenge' || response?.error || response?.ready === false) return true;
        if (response?.status !== 200 || !response.ready || response.authenticated !== true) return halt('page_unverified');
        await store({ recheckRecovery: { ...old, pendingAt: null, reason: 'recovered', completedAt: clock() }, connection: '재확인 페이지 갱신 완료 · 수집 재개' });
        if (active()) await resume();
        return true;
    }
    const normal = response?.status === 200 && response.ready && response.authenticated === true;
    const explicitChallenge = state.blockedCause === 'cloudflare_challenge' && Number.isFinite(state.blockedAt) && state.blockedAt <= clock() && clock() - state.blockedAt <= 60000;
    if (normal && !explicitChallenge) {
        if (!observing) await store({ recheckRecovery: { ...old, attempted: false, observedAt: now, pendingAt: null }, connection: '접근 거부 · 안내창 표시 대기 중' });
        return true;
    }
    if (!(response?.status === 403 && response.page_state === 'app_recheck') && !(normal && explicitChallenge)) return halt('not_app_recheck');
    // A persisted reservation survives worker restart and prevents reload loops.
    old = { attempted: true, pendingAt: now, tabId: tab.id, reloadCount: (Number.isInteger(old?.reloadCount) && old.reloadCount >= 0 ? old.reloadCount : 0) + 1, trigger: explicitChallenge ? 'challenged_response' : 'app_recheck' };
    await store({ recheckRecovery: old, connection: explicitChallenge ? '서버 보안 확인 응답 · 1회 새로고침 중' : '재확인 안내 · 1회 새로고침 중' });
    if (!active()) return true;
    try { await chrome.tabs.reload(tab.id); }
    catch { return halt('reload_failed'); }
    return true;
}
