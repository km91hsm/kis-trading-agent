import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { JSDOM } from '../../../web/node_modules/jsdom/lib/api.js';
const script = fs.readFileSync(new URL('../api-content.js', import.meta.url), 'utf8');
const TOKEN = 'a'.repeat(32);
// 구조만 본뜬 합성 로그인 화면: "이메일로 로그인" → 대화상자(이메일·비밀번호·로그인) → 프로필 메뉴.
const PAGE = '<body><header><button id="open">이메일로 로그인</button></header><main>뉴스</main></body>';
function page({ password = 'pw', dialogAfterClicks = 1, html = PAGE, state = { enabled: true, backend: 'api', ownerToken: TOKEN } } = {}) {
    const dom = new JSDOM(html, { url: 'https://www.saveticker.com/news#trader-saveticker-api=' + TOKEN, runScripts: 'outside-only' });
    const w = dom.window, d = w.document, seen = [];
    let listener, clicks = 0;
    w.chrome = { runtime: { id: 'ext', onMessage: { addListener: fn => listener = fn } }, storage: { local: { get: async () => state } } };
    w.setTimeout = fn => { Promise.resolve().then(fn); return 1; };
    w.clearTimeout = () => { };
    d.getElementById('open')?.addEventListener('click', () => {
        if (++clicks < dialogAfterClicks || d.querySelector('[role=dialog]')) return;
        const dialog = d.createElement('div'); dialog.setAttribute('role', 'dialog');
        dialog.innerHTML = '<h2>이메일로 로그인</h2><form><input type="email"><input type="password"><button type="button" id="submit">로그인</button></form>';
        d.body.append(dialog);
        for (const input of dialog.querySelectorAll('input')) input.addEventListener('input', e => seen.push(e.target.type));
        dialog.querySelector('#submit').addEventListener('click', () => {
            const [e, p] = dialog.querySelectorAll('input');
            if (e.value === 'u@x.com' && p.value === password) { dialog.remove(); const m = d.createElement('button'); m.setAttribute('aria-label', '프로필 메뉴'); d.body.append(m); }
        });
    });
    w.eval(script);
    const send = (message, sender = { id: 'ext' }) => new Promise(resolve => { const r = listener(message, sender, v => resolve(JSON.parse(JSON.stringify(v)))); if (!r) resolve(null); });
    return { w, d, seen, send, login: (extra = {}) => send({ type: 'api_login', token: TOKEN, email: 'u@x.com', password: 'pw', ...extra }) };
}

test('fills the email dialog with input events and reports logged_in once the profile menu appears', async () => {
    const p = page();
    assert.deepEqual(await p.login(), { result: 'logged_in' });
    assert.ok(p.d.querySelector('[aria-label="프로필 메뉴"]'));
    assert.deepEqual(p.seen.sort(), ['email', 'password']);
});

test('retries opening the dialog up to three clicks', async () => {
    assert.deepEqual(await page({ dialogAfterClicks: 3 }).login(), { result: 'logged_in' });
    assert.deepEqual(await page({ dialogAfterClicks: 4 }).login(), { result: 'login_form_missing' });
});

test('wrong password is login_failed; missing button is login_form_missing; already logged in is logged_in', async () => {
    assert.deepEqual(await page({ password: 'other' }).login(), { result: 'login_failed' });
    assert.deepEqual(await page({ html: '<body><main>뉴스</main></body>' }).login(), { result: 'login_form_missing' });
    assert.deepEqual(await page({ html: '<body><button aria-label="프로필 메뉴"></button></body>' }).login(), { result: 'logged_in' });
});

test('security check screen is never touched', async () => {
    const p = page({ html: '<body><h1>접속 환경을 확인하고 있습니다</h1><button id="open">이메일로 로그인</button></body>' });
    assert.deepEqual(await p.login(), { result: 'challenge' });
    assert.equal(p.d.querySelector('[role=dialog]'), null);
});

test('stopped collection, wrong token or foreign sender cannot log in', async () => {
    assert.deepEqual(await page({ state: { enabled: false, backend: 'api', ownerToken: TOKEN } }).login(), { error: 'api_stopped' });
    assert.deepEqual(await page().login({ token: 'b'.repeat(32) }), { error: 'api_stopped' });
    assert.deepEqual(await page().login({ email: 7 }), { error: 'invalid_message' });
    const p = page();
    assert.equal(await p.send({ type: 'api_login', token: TOKEN, email: 'u@x.com', password: 'pw' }, { id: 'other' }), null);
    assert.equal(p.d.querySelector('[role=dialog]'), null);
});
