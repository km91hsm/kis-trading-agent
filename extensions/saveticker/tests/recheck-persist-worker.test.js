import test from 'node:test';
import assert from 'node:assert/strict';
const event = () => ({ listeners: [], addListener(fn) { this.listeners.push(fn); } });
const TOKEN = 'a'.repeat(32);
function setup(state, { creds = { available: true, email: 'u@x.com', password: 'pw' } } = {}) {
    const calls = [], messages = event();
    globalThis.chrome = {
        storage: { local: { get: async keys => keys == null ? { ...state } : Object.fromEntries([keys].flat().filter(k => Object.hasOwn(state, k)).map(k => [k, state[k]])), set: async value => Object.assign(state, value) } },
        runtime: { id: 'ext', onMessage: messages, onStartup: event(), onInstalled: event(), connectNative: () => { const p = { onMessage: event(), onDisconnect: event(), disconnect() { }, postMessage(m) { calls.push(m); const r = m.type === 'credentials' ? { id: m.id, ok: true, type: 'credentials', ...creds } : { id: m.id, ok: true, views: [{ stream: 'news_all', kind: 'news', interval_s: 30 }], count: 1, status: 'partial', job_id: 'b'.repeat(32) }; queueMicrotask(() => p.onMessage.listeners.forEach(fn => fn(r))); } }; return p; } },
        alarms: { create: async () => { }, clear: async () => { }, onAlarm: event() },
        tabs: { query: async () => [{ id: 7, url: 'https://www.saveticker.com/news#trader-saveticker-api=' + TOKEN }], reload: async id => calls.push({ type: 'reload', id }), sendMessage: async (_id, m) => { calls.push(m); return m.type === 'api_login' ? { result: 'logged_in' } : { status: 200, ready: true, authenticated: false }; } },
        downloads: { onDeterminingFilename: event(), onChanged: event() }
    };
    return { calls, action: body => new Promise(resolve => messages.listeners[0](body, { id: 'ext' }, resolve)) };
}
test('persist setting needs auto recheck, is locked while collecting and clears repeat mode', async () => {
    const state = { enabled: false, backend: 'api', ownerToken: TOKEN, autoRecheck: false };
    const f = setup(state); await import('../worker.js?persist-setting');
    assert.deepEqual(await f.action({ action: 'set_persist_recheck', value: true }), { ok: false });
    await f.action({ action: 'set_auto_recheck', value: true });
    await f.action({ action: 'set_repeat_recheck', value: true });
    assert.deepEqual(await f.action({ action: 'set_persist_recheck', value: true }), { ok: true });
    assert.equal(state.persistRecheck, true); assert.equal(state.repeatRecheck, false);
    await f.action({ action: 'set_auto_recheck', value: false }); assert.equal(state.persistRecheck, false);
    state.autoRecheck = true; state.enabled = true;
    assert.deepEqual(await f.action({ action: 'set_persist_recheck', value: true }), { ok: false });
    state.enabled = false; await f.action({ action: 'stop' });
});
test('blocked worker in persist mode asks Native host for credentials only when logged out and resumes', async () => {
    const state = { enabled: false, backend: 'api', ownerToken: TOKEN, autoRecheck: true, persistRecheck: true, hostBlocked: true, blockedLanes: ['news'] };
    const f = setup(state); const worker = await import('../worker.js?persist-login'); state.enabled = true;
    await worker.tick();
    assert.equal(f.calls.filter(x => x.type === 'credentials').length, 1);
    const login = f.calls.find(x => x.type === 'api_login');
    assert.deepEqual({ ...login }, { type: 'api_login', token: TOKEN, email: 'u@x.com', password: 'pw' });
    assert.equal(state.hostBlocked, false); assert.equal(state.enabled, true);
    assert.ok(!JSON.stringify(state).includes('"pw"'));
    await f.action({ action: 'stop' });
});

test('unreadable credentials file from Native host stops persist recovery with its own reason', async () => {
    const state = { enabled: false, backend: 'api', ownerToken: TOKEN, autoRecheck: true, persistRecheck: true, hostBlocked: true, blockedLanes: ['news'] };
    const f = setup(state, { creds: { available: false, unreadable: true } }); const worker = await import('../worker.js?persist-unreadable'); state.enabled = true;
    await worker.tick();
    assert.equal(f.calls.filter(x => x.type === 'api_login').length, 0);
    const diag = f.calls.find(x => x.type === 'diagnostics');
    assert.equal(diag.reason, 'credentials_unreadable'); assert.equal(diag.terminal, true);
    assert.equal(state.enabled, false); assert.equal(state.recheckRecovery.reason, 'credentials_unreadable');
});
