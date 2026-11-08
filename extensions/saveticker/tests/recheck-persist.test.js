import test from 'node:test';
import assert from 'node:assert/strict';
import { recoverRecheck, backoffMs } from '../recheck.js';
const TOKEN = 'a'.repeat(32), NOW = 10_000_000, MIN = 60000;
const NORMAL = { status: 200, ready: true, authenticated: true }, OUT = { status: 200, ready: true, authenticated: false }, RECHECK = { status: 403, page_state: 'app_recheck' };
function fixture({ probe = RECHECK, login = { result: 'logged_in' }, creds = { email: 'u@x.com', password: 'pw' } } = {}) {
    const state = { autoRecheck: true, persistRecheck: true, backend: 'api', hostBlocked: true, recheckRecovery: null }, calls = [], sent = [], reports = [];
    const d = {
        state, token: TOKEN, now: NOW, active: () => true,
        store: async v => { Object.assign(state, v); },
        stop: async () => calls.push('stop'), resume: async () => calls.push('resume'),
        report: async r => { reports.push(r); calls.push('report:' + r.reason); },
        credentials: async () => { calls.push('creds'); return creds; },
        chrome: { tabs: {
            query: async () => [{ id: 7, url: 'https://www.saveticker.com/news#trader-saveticker-api=' + TOKEN }],
            sendMessage: async (_id, m) => { sent.push(m); if (m.type === 'api_login') { calls.push('login'); return login; } return f.probe; },
            reload: async () => calls.push('reload')
        } }
    };
    const f = { state, calls, sent, reports, d, probe, run: () => recoverRecheck(d), at: t => { d.now = t; d.clock = () => t; } };
    return f;
}
const count = (f, x) => f.calls.filter(c => c === x).length;

test('backoff grows 1,2,4,8,15 minutes then stays at 15', () => {
    assert.deepEqual([0, 1, 2, 3, 4, 5, 9].map(backoffMs), [1, 2, 4, 8, 15, 15, 15].map(m => m * MIN));
});

test('failed reload never stops collection; waits with growing gaps and keeps reloading', async () => {
    const f = fixture(); f.at(NOW);
    await f.run(); assert.equal(count(f, 'reload'), 1);
    // after reload the page is still the recheck screen → wait instead of stop
    f.at(NOW + 5000); await f.run();
    assert.equal(count(f, 'stop'), 0); assert.equal(f.state.hostBlocked, true);
    assert.equal(f.state.recheckRecovery.pendingAt, null); assert.equal(f.state.recheckRecovery.reason, 'page_unverified');
    assert.equal(f.state.recheckRecovery.nextAt, NOW + MIN);
    f.at(NOW + MIN - 1); await f.run(); assert.equal(count(f, 'reload'), 1, 'no reload before nextAt');
    let t = NOW + MIN; const gaps = [];
    for (let i = 0; i < 6; i++) {
        f.at(t); await f.run(); f.at(t + 46000); await f.run();
        gaps.push(f.state.recheckRecovery.nextAt - t); t = f.state.recheckRecovery.nextAt;
    }
    assert.deepEqual(gaps, [2, 4, 8, 15, 15, 15].map(m => m * MIN));
    assert.equal(count(f, 'reload'), 7); assert.equal(count(f, 'stop'), 0);
    assert.equal(f.state.recheckRecovery.reason, 'recovery_timeout');
});

test('verified page after reload resumes; failures reset only after 10 healthy minutes', async () => {
    const f = fixture(); f.at(NOW);
    await f.run(); f.probe = NORMAL; f.at(NOW + 3000); await f.run();
    assert.equal(f.calls.at(-1), 'resume'); assert.equal(f.state.recheckRecovery.reason, 'recovered');
    // blocked again soon: second attempt must wait the 1-minute gap
    f.probe = RECHECK; f.at(NOW + 20000); await f.run(); assert.equal(count(f, 'reload'), 1);
    f.at(NOW + MIN); await f.run(); assert.equal(count(f, 'reload'), 2);
    f.probe = NORMAL; f.at(NOW + MIN + 3000); await f.run(); assert.equal(count(f, 'resume'), 2);
    // healthy for 10 minutes → next incident reloads at once and gaps start over
    const done = f.state.recheckRecovery.completedAt; Object.assign(f.state, { apiHealthySince: done + 1000, lastApiHealthyAt: done + 1000 + 600000 });
    f.probe = RECHECK; f.at(done + 700000); await f.run();
    assert.equal(count(f, 'reload'), 3); assert.equal(f.state.recheckRecovery.failures, 1);
});

test('logged out page logs in with stored email/password and resumes', async () => {
    const f = fixture({ probe: OUT }); f.at(NOW); await f.run();
    assert.deepEqual(f.calls.filter(c => c !== 'report:undefined'), ['creds', 'login', 'resume']);
    const msg = f.sent.find(m => m.type === 'api_login');
    assert.deepEqual(msg, { type: 'api_login', token: TOKEN, email: 'u@x.com', password: 'pw' });
    assert.ok(!JSON.stringify(f.state).includes('pw'), 'password never persisted');
});

test('logged out after reload triggers login in the same attempt', async () => {
    const f = fixture(); f.at(NOW); await f.run();
    f.probe = OUT; f.at(NOW + 4000); await f.run();
    assert.deepEqual(f.calls, ['reload', 'creds', 'login', 'resume']);
});

test('missing credentials and three wrong passwords stop; other login problems wait', async () => {
    const none = fixture({ probe: OUT, creds: null }); none.at(NOW); await none.run();
    assert.equal(none.calls.at(-1), 'stop'); assert.ok(none.calls.includes('report:credentials_missing'));
    const form = fixture({ probe: OUT, login: { result: 'login_form_missing' } }); form.at(NOW); await form.run();
    assert.equal(count(form, 'stop'), 0); assert.equal(form.state.recheckRecovery.reason, 'login_form_missing');
    const bad = fixture({ probe: OUT, login: { result: 'login_failed' } }); let t = NOW;
    for (let i = 0; i < 3; i++) { bad.at(t); await bad.run(); t = bad.state.recheckRecovery.nextAt ?? t; }
    assert.equal(count(bad, 'login'), 3); assert.equal(count(bad, 'stop'), 1); assert.ok(bad.calls.includes('report:login_failed'));
});

test('missing owner tab and reload failure still stop', async () => {
    const gone = fixture(); gone.d.chrome.tabs.query = async () => []; gone.at(NOW); await gone.run();
    assert.equal(gone.calls.at(-1), 'stop');
    const fail = fixture(); fail.d.chrome.tabs.reload = async () => { throw Error('x'); }; fail.at(NOW); await fail.run();
    assert.equal(fail.calls.at(-1), 'stop');
});

test('busy, cooldown and inactive make no navigation in persist mode', async () => {
    for (const change of [f => f.d.busy = true, f => f.state.apiCooldownUntil = NOW + 10000, f => f.d.active = () => false]) {
        const f = fixture(); change(f); f.at(NOW); await f.run(); assert.equal(count(f, 'reload'), 0); assert.equal(count(f, 'stop'), 0);
    }
});

test('login timeout counts as a wrong-password attempt so lockout protection still works', async () => {
    const f = fixture({ probe: OUT, login: null }); let t = NOW;
    f.d.chrome.tabs.sendMessage = async (_id, m) => m.type === 'api_login' ? (f.calls.push('login'), null) : f.probe;
    for (let i = 0; i < 3; i++) { f.at(t); await f.run(); t = f.state.recheckRecovery.nextAt ?? t; }
    assert.equal(count(f, 'stop'), 1); assert.ok(f.calls.includes('report:login_failed'));
});

test('login failure count is consecutive: any recovery resets it', async () => {
    const f = fixture({ probe: OUT, login: { result: 'login_failed' } }); let t = NOW;
    for (let i = 0; i < 2; i++) { f.at(t); await f.run(); t = f.state.recheckRecovery.nextAt; }
    f.probe = RECHECK; f.at(t); await f.run(); f.probe = NORMAL; f.at(t + 3000); await f.run();
    assert.equal(f.state.recheckRecovery.reason, 'recovered'); assert.equal(f.state.recheckRecovery.loginFailures, 0);
    t = f.state.recheckRecovery.nextAt; f.probe = OUT; f.at(t); await f.run();
    assert.equal(count(f, 'stop'), 0);
});

test('login after reload clears the pending reload reservation first', async () => {
    const f = fixture(); f.at(NOW); await f.run();
    let pendingDuringLogin;
    f.d.chrome.tabs.sendMessage = async (_id, m) => { if (m.type === 'api_login') { pendingDuringLogin = f.state.recheckRecovery.pendingAt; return { result: 'login_failed' }; } return OUT; };
    f.at(NOW + 4000); await f.run();
    assert.equal(pendingDuringLogin, null);
});

test('unreadable credentials file stops with its own reason instead of missing', async () => {
    const f = fixture({ probe: OUT, creds: { unreadable: true } }); f.at(NOW); await f.run();
    assert.equal(f.calls.at(-1), 'stop'); assert.equal(count(f, 'login'), 0);
    assert.ok(f.calls.includes('report:credentials_unreadable'));
    assert.equal(f.state.recheckRecovery.reason, 'credentials_unreadable');
});

test('diagnostics say whether persist recovery stopped or only waits', async () => {
    const waiting = fixture(); waiting.at(NOW); await waiting.run(); waiting.at(NOW + 5000); await waiting.run();
    assert.deepEqual(waiting.reports.map(r => [r.reason, r.terminal]), [['page_unverified', false]]);
    const halted = fixture(); halted.d.chrome.tabs.query = async () => []; halted.at(NOW); await halted.run();
    assert.deepEqual(halted.reports.map(r => [r.reason, r.terminal]), [['owner_unverified', true]]);
});

test('worker dying after recovered save but before resume retries resume without reloading', async () => {
    const f = fixture({ probe: NORMAL }); f.at(NOW + 5000);
    f.state.blockedAt = NOW;
    f.state.recheckRecovery = { persist: true, failures: 1, reloadCount: 1, loginFailures: 0, nextAt: NOW + MIN, pendingAt: null, tabId: null, reason: 'recovered', completedAt: NOW + 3000 };
    await f.run();
    assert.deepEqual(f.calls, ['resume']);
    // 그 뒤 새 차단이 기록됐으면 평소대로 간격을 기다린다
    const g = fixture(); g.at(NOW + 5000);
    g.state.blockedAt = NOW + 4000;
    g.state.recheckRecovery = { persist: true, failures: 1, reloadCount: 1, loginFailures: 0, nextAt: NOW + MIN, pendingAt: null, tabId: null, reason: 'recovered', completedAt: NOW + 3000 };
    await g.run();
    assert.deepEqual(g.calls, []);
});
