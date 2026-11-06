import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { JSDOM } from '../../../web/node_modules/jsdom/lib/api.js';
test('popup restores resume after start and repeated resume, and identifies cached worker', async () => {
    const dom = new JSDOM(fs.readFileSync(new URL('../popup.html', import.meta.url), 'utf8'), { runScripts: 'outside-only' });
    const state = { enabled: false, pdfState: 'pdf_begin_busy' }, actions = [];
    dom.window.chrome = {
        storage: { local: { get: async () => ({ ...state }) }, onChanged: { addListener() { } } },
        runtime: { sendMessage: async ({ action }) => { actions.push(action); state.enabled = action !== 'stop'; } }
    };
    const settle = () => new Promise(resolve => setTimeout(resolve, 0));
    dom.window.eval(fs.readFileSync(new URL('../popup.js', import.meta.url), 'utf8'));
    await settle();
    const get = id => dom.window.document.getElementById(id);
    assert.match(get('version').textContent, /수집기 시작 전/);
    assert.doesNotMatch(get('version').textContent, /새로고침 필요/);
    assert.match(get('pdf-state').textContent, /발급.*pdf_begin_busy/);
    state.pdfState = 'PRIVATE https://secret.test';
    get('start').click();
    await settle();
    assert.equal(get('pdf-state').textContent, '');
    state.pdfState = 'pdf_save_path';
    assert.equal(get('resume').disabled, false);
    get('resume').click();
    await settle();
    assert.match(get('pdf-state').textContent, /저장.*pdf_save_path/);
    assert.equal(get('resume').disabled, false);
    get('resume').click();
    await settle();
    assert.deepEqual(actions, ['start', 'resume', 'resume']);
    get('stop').click();
    await settle();
    assert.equal(get('start').disabled, false);
    assert.equal(get('resume').disabled, false);
    dom.window.close();
});
test('popup shows API failure stage and never reports no errors for a failed PDF', async () => {
    const dom = new JSDOM(fs.readFileSync(new URL('../popup.html', import.meta.url), 'utf8'), { runScripts: 'outside-only' });
    const state = { backend: 'api', pdfState: 'api_failed', pdfError: 'api_response_unverified', pdfErrorStage: 'fetch', lane_error_news: 'api_shape_unverified', lane_error_stage_news: 'prepare' };
    let render;
    dom.window.chrome = { storage: { local: { get: async () => ({ ...state }) }, onChanged: { addListener(fn) { render=fn; } } } };
    dom.window.eval(fs.readFileSync(new URL('../popup.js', import.meta.url), 'utf8'));
    await new Promise(resolve => setTimeout(resolve, 0));
    const diagnostics = () => dom.window.document.getElementById('diagnostics').textContent;
    assert.match(diagnostics(), /api_shape_unverified/);
    assert.match(diagnostics(), /본문 준비/);
    assert.match(diagnostics(), /PDF.*응답 수신.*api_response_unverified/);
    assert.doesNotMatch(diagnostics(), /오류가 없습니다/);
    state.backend='dom';state.lane_error_news='content_unavailable';await render();assert.doesNotMatch(diagnostics(), /본문 준비/);
    state.pdfError='PRIVATE secret';state.pdfErrorStage='PRIVATE secret';state.lane_error_stage_news='PRIVATE secret';
    await render();
    assert.doesNotMatch(diagnostics(), /PRIVATE/);
    assert.doesNotMatch(diagnostics(), /오류가 없습니다/);
    dom.window.close();
});
