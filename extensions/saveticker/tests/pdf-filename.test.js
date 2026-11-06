import test from 'node:test';
import assert from 'node:assert/strict';
import { PdfPair } from '../core.js';

const at = 1800000000000;
const name = 'a'.repeat(32) + '-1800000000000.pdf';
const url = 'blob:https://www.saveticker.com/synthetic-report';
function setup() {
    const pair = new PdfPair();
    pair.begin({ direct: true, download_name: name }, at);
    pair.expect(url);
    return pair;
}
const item = () => ({ id: 7, byExtensionId: 'collector', url, mime: 'application/pdf', startTime: new Date(at + 1).toISOString() });

test('filename suggestion preserves download API ownership before and after ID binding', () => {
    const pair = setup();
    assert.equal(pair.filename(item(), 'collector', at + 2), 'SaveTicker/' + name);
    assert.equal(pair.pending.downloadId, null, 'filename events must never claim an ID');
    pair.bind(7);
    assert.equal(pair.filename(item(), 'collector', at + 2), 'SaveTicker/' + name);
    assert.equal(pair.filename({ ...item(), id: 8 }, 'collector', at + 2), null);
});

test('unrelated, expired and cancelled downloads keep their original filename', () => {
    const pair = setup();
    for (const changed of [
        { byExtensionId: 'other' }, { byExtensionId: undefined },
        { url: 'blob:https://www.saveticker.com/other' },
        { url: 'blob:https://evil.invalid/synthetic-report' },
        { mime: 'text/html' }, { id: -1 }, { id: 1.5 },
        { startTime: 'invalid' }, { startTime: new Date(at - 1).toISOString() },
        { startTime: new Date(at + 120001).toISOString() },
    ]) assert.equal(pair.filename({ ...item(), ...changed }, 'collector', at + 2), null);
    assert.equal(pair.filename(item(), 'collector', at + 120001), null);
    assert.equal(pair.filename(item(), '', at + 2), null);
    pair.pending.cancelled = true;
    assert.equal(pair.filename(item(), 'collector', at + 2), null);
    pair.pending.cancelled = false;
    pair.pending.direct = false;
    assert.equal(pair.filename(item(), 'collector', at + 2), null);
    pair.clear();
    assert.equal(pair.filename(item(), 'collector', at + 2), null);
});

test('Native ticket expiry takes precedence over the local two-minute window', () => {
    const pair = setup();
    pair.pending.expires_at = new Date(at + 110000).toISOString();
    const late = { ...item(), startTime: new Date(at + 115000).toISOString() };
    assert.equal(pair.filename(late, 'collector', at + 115000), null);
    assert.equal(pair.filename(item(), 'collector', at + 110000), null);
    pair.pending.expires_at = 'invalid';
    assert.equal(pair.filename(item(), 'collector', at + 2), null);
    delete pair.pending.expires_at;
    assert.equal(pair.filename(item(), 'collector', at + 120000), null);
});
