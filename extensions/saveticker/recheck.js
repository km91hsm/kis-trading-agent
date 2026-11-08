import { repeatEligible } from './recovery-health.js';
import { ownsApiTab } from './core.js';
const BACKOFF_MIN = [1, 2, 4, 8, 15], HEALTHY_RESET = 600000, PENDING = 45000, LOGIN_FAILURE_LIMIT = 3;
// failures 번째(0부터) 시도 뒤 다음 시도까지의 간격: 1→2→4→8→15분, 이후 15분.
export const backoffMs = failures => BACKOFF_MIN[Math.min(Math.max(0, failures | 0), BACKOFF_MIN.length - 1)] * 60000;
const count = v => Number.isSafeInteger(v) && v >= 0 ? v : 0;
async function probeTab(chrome, tab, token, ms) {
    let timer;
    try {
        return await Promise.race([chrome.tabs.sendMessage(tab.id, { type: 'api_probe', token }), new Promise(resolve => { timer = setTimeout(() => resolve(null), ms); })]);
    } catch { return null; } finally { clearTimeout(timer); }
}
// 계속 복구: 구조적 문제(전용 탭 없음·새로고침 불가·로그인 정보 없음·비밀번호 3회 오류)만 중지하고,
// 나머지는 간격을 늘려 가며 정상 새로고침·이메일 로그인을 반복한다. 보안 확인 UI 는 조작하지 않는다.
async function persistRecover({ chrome, state, token, active, store, stop, resume, report, busy, now, clock, credentials }) {
    const raw = state.recheckRecovery;
    let old = raw && typeof raw === 'object' && raw.persist === true ? { ...raw } : { persist: true };
    for (const k of ['failures', 'reloadCount', 'loginFailures']) old[k] = count(old[k]);
    if (!(Number.isFinite(old.nextAt) && old.nextAt <= now + backoffMs(99))) old.nextAt = null;
    if (!(Number.isFinite(old.pendingAt) && old.pendingAt <= now && Number.isInteger(old.tabId))) { old.pendingAt = null; old.tabId = null; }
    if (old.reason === 'recovered' && old.pendingAt === null) {
        const since = state.apiHealthySince, last = state.lastApiHealthyAt;
        if (Number.isFinite(since) && Number.isFinite(last) && since > 0 && since >= old.completedAt && last - since >= HEALTHY_RESET) { old.failures = 0; old.loginFailures = 0; old.nextAt = null; }
    }
    // terminal: 중지(true)와 재시도 대기(false)를 진단 파일에서 구분한다.
    const report_ = async (reason, terminal) => { if (!active()) return; try { await report({ category: 'recheck', reason, reload_count: old.reloadCount, terminal }); } catch { /* 진단 실패는 복구를 막지 않는다. */ } };
    const halt = async reason => {
        if (!active()) return true;
        await store({ recheckRecovery: { ...old, pendingAt: null, reason }, connection: '재확인 자동 복구 중지 · 직접 확인 필요' });
        await report_(reason, true);
        if (active()) await stop();
        return true;
    };
    const wait = async reason => {
        if (!active()) return true;
        old = { ...old, pendingAt: null, tabId: null, reason };
        const minutes = Math.max(1, Math.ceil(((old.nextAt ?? now) - now) / 60000));
        await store({ recheckRecovery: old, connection: '복구 재시도 대기 · 약 ' + minutes + '분 뒤' });
        await report_(reason, false);
        return true;
    };
    const recovered = async () => {
        if (!active()) return true;
        // 어떤 방식이든 복구되면 비밀번호 오류 횟수도 '연속'이 끊긴다.
        await store({ recheckRecovery: { ...old, pendingAt: null, tabId: null, loginFailures: 0, reason: 'recovered', completedAt: clock() }, connection: '재확인 복구 완료 · 수집 재개' });
        if (active()) await resume();
        return true;
    };
    const login = async tab => {
        const account = await credentials();
        if (!active()) return true;
        if (account === undefined) return wait('content_unavailable');
        if (account?.unreadable === true) return halt('credentials_unreadable');
        if (!account || typeof account.email !== 'string' || typeof account.password !== 'string' || !account.email || !account.password) return halt('credentials_missing');
        // 새로고침 예약을 먼저 지운다(로그인 중 워커가 재시작돼도 같은 시도에서 로그인을 되풀이하지 않는다).
        old = { ...old, pendingAt: null, tabId: null };
        await store({ recheckRecovery: old, connection: '로그아웃 감지 · 이메일 로그인 중' });
        let timer, result;
        try {
            result = await Promise.race([chrome.tabs.sendMessage(tab.id, { type: 'api_login', token, email: account.email, password: account.password }), new Promise(resolve => { timer = setTimeout(() => resolve(null), 40000); })]);
        } catch { result = null; } finally { clearTimeout(timer); }
        if (!active()) return true;
        if (result?.result === 'logged_in') { old.loginFailures = 0; return recovered(); }
        // 시간 초과는 제출 여부를 알 수 없으므로 비밀번호 오류로 센다(계정 잠금 방지가 우선).
        if (result?.result === 'login_failed' || result === null) {
            old.loginFailures += 1;
            if (old.loginFailures >= LOGIN_FAILURE_LIMIT) return halt('login_failed');
            return wait('login_failed');
        }
        return wait(result?.result === 'challenge' ? 'page_unverified' : 'login_form_missing');
    };
    // 「recovered」 저장 뒤 resume() 전에 워커가 죽었으면(그 뒤 새 차단 기록 없음) 새로고침 없이 재개만 다시 한다.
    if (old.reason === 'recovered' && old.pendingAt === null && Number.isFinite(old.completedAt) && Number.isFinite(state.blockedAt) && state.blockedAt <= old.completedAt) {
        if (active()) await resume();
        return true;
    }
    const ownedTab = async () => {
        const tabs = (await chrome.tabs.query({ url: 'https://www.saveticker.com/*' })).filter(t => ownsApiTab(t, token));
        return tabs.length === 1 ? tabs[0] : null;
    };
    if (old.pendingAt !== null) {
        if (now - old.pendingAt >= PENDING) return wait('recovery_timeout');
        const tab = await ownedTab();
        if (!active()) return true;
        if (!tab || tab.id !== old.tabId) return halt('owner_unverified');
        const response = await probeTab(chrome, tab, token, Math.min(10000, Math.max(1, old.pendingAt + PENDING - clock())));
        if (!active()) return true;
        if (!response || response.error || response.page_state === 'challenge' || response.ready === false) return clock() - old.pendingAt >= PENDING ? wait('recovery_timeout') : true;
        if (response.status === 200 && response.authenticated === true) return recovered();
        if (response.status === 200) return login(tab);
        return wait('page_unverified');
    }
    if (old.nextAt !== null && now < old.nextAt) {
        if (raw !== old && JSON.stringify(raw) !== JSON.stringify(old)) await store({ recheckRecovery: old });
        return true;
    }
    if (busy || Number.isFinite(state.apiCooldownUntil) && now < state.apiCooldownUntil) return true;
    const tab = await ownedTab();
    if (!active()) return true;
    if (!tab) return halt('owner_unverified');
    const response = await probeTab(chrome, tab, token, 10000);
    if (!active()) return true;
    // 시도 1회를 예약한 뒤 행동한다(워커 재시작에도 같은 시도를 되풀이하지 않는다).
    old = { ...old, reason: null, nextAt: now + backoffMs(old.failures), failures: old.failures + 1 };
    if (response?.status === 200 && response.ready && response.authenticated !== true && !response.page_state) {
        await store({ recheckRecovery: old });
        return login(tab);
    }
    old = { ...old, pendingAt: now, tabId: tab.id, reloadCount: old.reloadCount + 1 };
    await store({ recheckRecovery: old, connection: '재확인 복구 · 새로고침 중 (' + old.failures + '번째)' });
    if (!active()) return true;
    try { await chrome.tabs.reload(tab.id); }
    catch { return halt('reload_failed'); }
    return true;
}
export async function recoverRecheck({ chrome, state, token, active, store, stop, resume, report = async () => {}, busy = false, now = Date.now(), clock, credentials = async () => null }) {
    if (!active() || !state.autoRecheck || state.backend !== 'api' || !state.hostBlocked) return false;
    if (state.persistRecheck === true) {
        const began = performance.now();
        return persistRecover({ chrome, state, token, active, store, stop, resume, report, busy, now, clock: clock || (() => now + Math.max(0, performance.now() - began)), credentials });
    }
    const began = performance.now();
    clock ||= () => now + Math.max(0, performance.now() - began);
    let old = state.recheckRecovery;
    const halt = async reason => {
        if (active()) {
            await store({ recheckRecovery: { ...old, attempted: true, pendingAt: null, reason }, connection: '재확인 자동 복구 중지 · 직접 확인 필요' });
            if (active()) {
                try { await report({ category: 'recheck', reason, reload_count: old?.reloadCount ?? 0, terminal: true }); }
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
