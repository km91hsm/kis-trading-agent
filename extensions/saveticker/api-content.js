// Isolated browser-managed same-origin reads. No MAIN hooks or credential reads.
(() => {
    const ORIGIN = 'https://www.saveticker.com', MAX = 2097152, PDF_MAX = 25 * 1024 * 1024;
    const KEYS = new Set('news_list reports report total_count page page_size events rankings items id title source name channel created_at is_headline_only content translations translated ko_KR tickers symbol content_labels label_name event_date event_date_only rank nameKo labelKo price changePercent turnover ticker regular text children body has_pdf pdf_url tab basis session excludeRisky marketStatus'.split(' '));
    let pending = null;
    const blobs = new Map();
    function release(nonce) {
        const entry = blobs.get(nonce);
        if (!entry) return;
        blobs.delete(nonce); clearTimeout(entry.timer); URL.revokeObjectURL(entry.url);
    }
    function project(value, depth = 0) {
        if (depth > 8) return null;
        if (typeof value === 'string') return value.slice(0, 200000);
        if (value === null || typeof value === 'boolean' || typeof value === 'number') return value;
        if (Array.isArray(value)) return value.slice(0, 501).map(v => project(v, depth + 1));
        if (typeof value === 'object') return Object.fromEntries(Object.entries(value).filter(([k]) => KEYS.has(k)).map(([k, v]) => [k, project(v, depth + 1)]));
        return null;
    }
    function allowed(r) {
        if (r?.method !== 'GET' || r.max_bytes !== (r.kind === 'pdf' ? PDF_MAX : MAX)) return false;
        try {
            const u = new URL(r.url);
            if (u.origin !== ORIGIN || u.username || u.password || u.hash) return false;
            const fixed = {
                news: '/api/news/list?page=1&page_size=20&sort=created_at_desc&label_group=1&label_name=1',
                reports: '/api/report/list?page=1&page_size=20',
                groups: '/api/stocks/api/v1/watchlist/groups',
                top5: '/api/stocks/api/v1/community/feed/ticker-rankings?limit=5',
                index: '/api/stocks/api/v1/market-panel'
            };
            if (Object.hasOwn(fixed, r.kind)) return u.href === ORIGIN + fixed[r.kind];
            if (r.kind === 'news_detail' || r.kind === 'report_detail') {
                const report = r.kind === 'report_detail';
                return u.pathname === (report ? '/api/report/detail' : '/api/news/detail') && (report ? /^\?id=\d{1,20}$/ : /^\?id=[A-Za-z0-9_-]{1,128}$/).test(u.search);
            }
            if (r.kind === 'pdf') {
                const path = u.searchParams.get('url');
                return u.pathname === '/api/report/download' && /^\/api\/uploads\/documents\/\d{1,20}$/.test(path || '') && u.search === '?' + new URLSearchParams({ url: path }).toString();
            }
            if (r.kind === 'popular') {
                return u.pathname === '/api/stocks/api/v1/tickers/popular' && /^\?tab=(gainers|losers)&groupId=[1-9]\d{0,15}(?:&basis=regular)?&excludeRisky=true$/.test(u.search) || u.pathname === '/api/stocks/api/v1/tickers/popular' && /^\?tab=turnover&groupId=[1-9]\d{0,15}(?:&basis=regular)?$/.test(u.search);
            }
            if (r.kind !== 'calendar' || u.pathname !== '/api/calendar/events' || !/^\?start_date=\d{4}-\d{2}-\d{2}&end_date=\d{4}-\d{2}-\d{2}$/.test(u.search)) return false;
            const start = Date.parse(u.searchParams.get('start_date')), end = Date.parse(u.searchParams.get('end_date'));
            return Number.isFinite(start) && Number.isFinite(end) && end > start && end - start <= 33 * 86400000;
        } catch { return false; }
    }
    async function live(token) {
        const s = await chrome.storage.local.get(['enabled', 'ownerToken', 'backend']);
        return s.enabled === true && s.backend === 'api' && s.ownerToken === token && location.hash === '#trader-saveticker-api=' + token;
    }
    function barrier() {
        const heading = document.querySelector('h1')?.textContent?.trim();
        return /^(잠시만 기다려 주세요|Just a moment)/i.test(document.title) || heading === '접속 환경을 확인하고 있습니다' || [...(document.querySelectorAll?.('h1,h2,h3,[role=heading]') || [])].slice(0, 256).some(e => ['재확인이 필요합니다', '접속 환경을 확인하고 있습니다'].includes(e.textContent?.trim()));
    }
    // 계속 복구 중 로그아웃이면 사이트의 일반 이메일 로그인 대화상자를 채운다. 보안 확인 화면은 건드리지 않고,
    // 비밀번호는 입력란에만 넣으며 저장·반환하지 않는다. 결과는 고정 코드만 돌려준다.
    const pause = () => new Promise(resolve => setTimeout(resolve, 200));
    async function waitFor(check, ms) {
        for (let i = 0; i <= ms / 200; i++) { const v = check(); if (v) return v; await pause(); }
        return null;
    }
    const exact = (selector, label, root = document) => [...root.querySelectorAll(selector)].slice(0, 512).find(e => e.textContent?.trim() === label) || null;
    const authenticated = () => !!document.querySelector('[aria-label="프로필 메뉴"]');
    function fill(input, value) {
        const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(input), 'value')?.set;
        input.focus?.();
        setter ? setter.call(input, value) : input.value = value;
        input.dispatchEvent(new input.ownerDocument.defaultView.Event('input', { bubbles: true }));
        input.dispatchEvent(new input.ownerDocument.defaultView.Event('change', { bubbles: true }));
    }
    async function login(email, password) {
        if (barrier()) return 'challenge';
        if (authenticated()) return 'logged_in';
        const opener = await waitFor(() => exact('button,[role=button]', '이메일로 로그인'), 15000);
        if (!opener) return barrier() ? 'challenge' : authenticated() ? 'logged_in' : 'login_form_missing';
        let form = null;
        for (let i = 0; i < 3 && !form; i++) {
            if (barrier()) return 'challenge';
            opener.click();
            form = await waitFor(() => exact('h1,h2,h3,[role=heading]', '이메일로 로그인') && document.querySelector('input[type="password"]'), 2000);
        }
        if (!form) return barrier() ? 'challenge' : 'login_form_missing';
        const scope = form.form || form.closest('[role=dialog]') || document;
        const id = scope.querySelector('input[type="email"], input[autocomplete="email"], input[type="text"]');
        const submit = exact('button', '로그인', scope);
        if (!id || !submit) return 'login_form_missing';
        fill(id, email); fill(form, password);
        await pause();
        if (submit.disabled) return 'login_failed';
        submit.click();
        if (await waitFor(authenticated, 20000)) return 'logged_in';
        return barrier() ? 'challenge' : 'login_failed';
    }
    chrome.runtime.onMessage.addListener((message, sender, reply) => {
        if (sender.id !== chrome.runtime.id) return;
        if (message.type === 'api_pdf_release') {
            if (/^[a-f0-9]{32}$/.test(message.nonce || '') && location.hash === '#trader-saveticker-api=' + message.token) release(message.nonce);
            reply({ ok: true }); return;
        }
        if (message.type === 'api_abort') {
            if (message.token && location.hash === '#trader-saveticker-api=' + message.token) pending?.controller.abort();
            reply({ ok: true }); return;
        }
        if (message.type === 'api_login') {
            (async () => {
                if (!/^[a-f0-9]{32}$/.test(message.token || '') || !await live(message.token)) return { error: 'api_stopped' };
                if (typeof message.email !== 'string' || typeof message.password !== 'string' || !message.email || !message.password || message.email.length > 320 || message.password.length > 1024) return { error: 'invalid_message' };
                return { result: await login(message.email, message.password) };
            })().then(reply).catch(() => reply({ result: 'login_form_missing' }));
            return true;
        }
        if (message.type === 'api_probe') {
            (async () => {
                if (!/^[a-f0-9]{32}$/.test(message.token || '') || !await live(message.token)) return { error: 'api_stopped' };
                if (barrier()) {
                    const app = !/^(잠시만 기다려 주세요|Just a moment)/i.test(document.title) && [...(document.querySelectorAll?.('h1,h2,h3,[role=heading]') || [])].slice(0, 256).some(e => e.textContent?.trim() === '재확인이 필요합니다');
                    return { status: 403, page_state: app ? 'app_recheck' : 'challenge' };
                }
                return { status: 200, ready: ['interactive', 'complete'].includes(document.readyState), authenticated: !!document.querySelector('[aria-label="프로필 메뉴"]') };
            })().then(reply).catch(() => reply({ error: 'api_probe_failed' }));
            return true;
        }
        if (message.type !== 'api_fetch') return;
        (async () => {
            const { token, job } = message;
            if (!/^[a-f0-9]{32}$/.test(token || '') || location.origin !== ORIGIN || location.hash !== '#trader-saveticker-api=' + token) return { error: 'api_denied' };
            if (!allowed(job?.request) || !/^[a-f0-9]{32}$/.test(job?.job_id || '') || !Number.isFinite(Date.parse(job.expires_at)) || Date.parse(job.expires_at) <= Date.now() || Date.parse(job.expires_at) > Date.now() + 120000) return { error: 'api_contract_unverified' };
            if (!await live(token)) return { error: 'api_stopped' };
            if (barrier()) return { status: 403 };
            if (pending || job.request.kind === 'pdf' && blobs.size) return { error: 'api_busy' };
            const own = { controller: new AbortController() }; pending = own;
            const timer = setTimeout(() => own.controller.abort(), Math.min(15000, Date.parse(job.expires_at) - Date.now()));
            try {
                const r = await fetch(job.request.url, { method: 'GET', credentials: 'same-origin', redirect: 'manual', cache: 'no-store', signal: own.controller.signal });
                if (!await live(token)) return { error: 'api_stopped' };
                if (r.type === 'opaqueredirect' || r.status >= 300 && r.status < 400 || r.url !== job.request.url || r.redirected) return { status: 403 };
                if (r.status !== 429 && r.headers.get('cf-mitigated') === 'challenge' && (r.headers.get('content-type') || '').split(';')[0].trim().toLowerCase() === 'text/html') return { status: 403, security: 'cloudflare_challenge' };
                if ([401, 403, 429].includes(r.status)) return { status: r.status, retry_after: r.status === 429 ? r.headers.get('retry-after') : null };
                const type = (r.headers.get('content-type') || '').split(';')[0].trim().toLowerCase(), pdf = job.request.kind === 'pdf';
                if (type === 'text/html') return { status: 403 };
                if (r.status !== 200 || type !== (pdf ? 'application/pdf' : 'application/json')) return { error: 'api_response_unverified' };
                if (Number(r.headers.get('content-length')) > job.request.max_bytes) return { error: 'api_response_too_large' };
                const reader = r.body.getReader(), chunks = []; let length = 0;
                try {
                    while (true) {
                        const chunk = await reader.read(); if (chunk.done) break;
                        length += chunk.value.byteLength;
                        if (length > job.request.max_bytes) { await reader.cancel(); return { error: 'api_response_too_large' }; }
                        chunks.push(chunk.value);
                    }
                } finally { reader.releaseLock(); }
                if (!await live(token) || own.controller.signal.aborted) return { error: 'api_stopped' };
                if (pdf) {
                    const blob = new Blob(chunks, { type: 'application/pdf' });
                    if (new TextDecoder().decode(await blob.slice(0, 5).arrayBuffer()) !== '%PDF-') return { error: 'api_pdf_unverified' };
                    if (!await live(token) || own.controller.signal.aborted) return { error: 'api_stopped' };
                    const url = URL.createObjectURL(blob), entry = { url, timer: setTimeout(() => release(job.job_id), 120000) };
                    entry.timer?.unref?.(); blobs.set(job.job_id, entry);
                    return { url: r.url, status: 200, content_type: type, blob_url: url, nonce: job.job_id };
                }
                const bytes = new Uint8Array(length); let offset = 0;
                for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
                const data = JSON.parse(new TextDecoder().decode(bytes));
                if (!data || typeof data !== 'object' || Array.isArray(data)) return { error: 'api_shape_unverified' };
                if (job.request.kind === 'groups') {
                    if (!Array.isArray(data.groups) || data.groups.length > 128 || data.groups.some(g => !g || typeof g !== 'object')) return { error: 'api_context_unverified' };
                    return { url: r.url, status: 200, content_type: type, data: { groups: data.groups.map(g => ({ groupId: g.groupId, isDefault: g.isDefault })) } };
                }
                return { url: r.url, status: r.status, content_type: type, data: project(data) };
            } finally { clearTimeout(timer); if (pending === own) pending = null; }
        })().then(reply).catch(() => reply({ error: 'api_request_failed' }));
        return true;
    });
})();
