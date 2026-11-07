"""Real minimized Chrome reload, production worker/probe, synthetic HTTPS only."""

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


@pytest.mark.parametrize("recovery", [False, True, "delayed", "server", "repeat"])
def test_normal_html_reload_keeps_window_minimized_and_never_replays(tmp_path, recovery):
    source = Path(__file__).resolve().parents[3] / "extensions/saveticker"
    extension = tmp_path / "extension"
    shutil.copytree(source, extension)
    path = extension / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["permissions"].remove("nativeMessaging")
    manifest["background"]["service_worker"] = "html-probe-worker.js"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    (extension / "html-native.js").write_text(
        """
const event=()=>({listeners:[],addListener(fn){this.listeners.push(fn)}});
globalThis.htmlCalls=[];
chrome.runtime.connectNative=()=>{const p={onMessage:event(),onDisconnect:event(),disconnect(){},
 postMessage(m){
 htmlCalls.push(m);const r={id:m.id,ok:true,count:1,status:'partial'};
 if(m.type==='hello')r.views=[{stream:'news_all',kind:'news',interval_s:30}];
 if(m.type==='api_begin')Object.assign(r,{job_id:'b'.repeat(32),expires_at:new Date(Date.now()+120000).toISOString(),request:{method:'GET',kind:'news',max_bytes:2097152,url:'https://www.saveticker.com/api/news/list?page=1&page_size=20&sort=created_at_desc&label_group=1&label_name=1'}});
 queueMicrotask(()=>p.onMessage.listeners.forEach(fn=>fn(r)));
}};return p;};
""",
        encoding="utf-8",
    )
    probe = """
import './html-native.js';import {tick} from './worker.js';
import {beginHtmlTrial} from './html-refresh.js';
globalThis.htmlReady=true;globalThis.htmlTick=tick;
globalThis.beginHtmlProbe=async()=>{const [tab]=await chrome.tabs.query({url:'https://www.saveticker.com/*'});
 await chrome.windows.update(tab.windowId,{state:'minimized'});
 await chrome.storage.local.set({enabled:true,backend:'api',apiWindowId:tab.windowId,
 ownerToken:'e'.repeat(32),
 htmlRefreshTest:true,htmlRefreshTrial:beginHtmlTrial(Date.now()-240001),lastApiHealthyAt:Date.now()});
 await tick();return chrome.storage.local.get(null);};
"""
    if recovery:
        probe = probe.replace(
            "htmlRefreshTest:true,htmlRefreshTrial:beginHtmlTrial(Date.now()-240001)",
            "htmlRefreshTest:false,autoRecheck:true,hostBlocked:true,blockedLanes:['news']",
        )
    if recovery == "server":
        probe = probe.replace(
            "autoRecheck:true,hostBlocked:true",
            "autoRecheck:true,hostBlocked:true,blockedAt:Date.now(),blockedCause:'cloudflare_challenge'",
        )
    if recovery == "repeat":
        probe = probe.replace(
            "autoRecheck:true,hostBlocked:true",
            "autoRecheck:true,hostBlocked:true,repeatRecheck:true,keepMinimized:true",
        )
    (extension / "html-probe-worker.js").write_text(probe, encoding="utf-8")
    profile = tmp_path / "profile"
    profile.mkdir()
    with sync_playwright() as playwright:
        if not Path(playwright.chromium.executable_path).is_file():
            pytest.skip("Chromium not installed")
        proc = subprocess.Popen(
            [
                playwright.chromium.executable_path,
                f"--user-data-dir={profile}",
                "--remote-debugging-port=0",
                "--no-first-run",
                "--headless=new",  # 실행 중 화면에 창을 띄우지 않는다(확장 지원 headless)
                "--no-default-browser-check",
                "--disable-background-networking",
                "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost",
                f"--disable-extensions-except={extension}",
                f"--load-extension={extension}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        browser = None
        try:
            endpoint = profile / "DevToolsActivePort"
            deadline = time.monotonic() + 10
            while not endpoint.exists():
                assert time.monotonic() < deadline, "browser startup"
                time.sleep(0.05)
            port = endpoint.read_text(encoding="utf-8").splitlines()[0]
            browser = playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{port}", no_defaults=True
            )
            context = browser.contexts[0]
            html_requests = []
            api_requests = []

            def route(request):
                url = request.request.url
                if url.startswith("chrome-extension://"):
                    request.continue_()
                elif url == "https://www.saveticker.com/news":
                    html_requests.append(url)
                    body = (
                        '<meta charset="utf-8"><title>Healthy page</title>'
                        '<button aria-label="프로필 메뉴">Profile</button>'
                    )
                    if recovery and recovery != "server" and len(html_requests) == 1:
                        body = (
                            '<meta charset="utf-8"><title>News</title><h1>재확인이 필요합니다</h1>'
                        )
                    if recovery == "delayed" and len(html_requests) == 1:
                        body = (
                            '<meta charset="utf-8"><title>News</title>'
                            '<button aria-label="프로필 메뉴">Profile</button>'
                            '<script>setTimeout(()=>{const h=document.createElement("h3");'
                            'h.textContent="재확인이 필요합니다";'
                            "document.body.append(h)},600)</script>"
                        )
                    request.fulfill(content_type="text/html", body=body)
                elif url.startswith("https://www.saveticker.com/api/news/list?"):
                    api_requests.append(url)
                    request.fulfill(
                        content_type="application/json",
                        body=json.dumps(
                            {"news_list": [{"id": "html_probe", "title": "Synthetic news"}]}
                        ),
                    )
                else:
                    request.abort()

            context.route("**/*", route)
            page = context.new_page()
            page.goto("https://www.saveticker.com/news#trader-saveticker-api=" + "e" * 32)
            worker = next(
                (w for w in context.service_workers if w.url.endswith("/html-probe-worker.js")),
                None,
            )
            if worker is None:
                worker = context.wait_for_event(
                    "serviceworker",
                    predicate=lambda w: w.url.endswith("/html-probe-worker.js"),
                    timeout=10000,
                )
            deadline = time.monotonic() + 10
            while not worker.evaluate("()=>globalThis.htmlReady===true"):
                assert time.monotonic() < deadline, "worker ready"
                time.sleep(0.1)
            # document_idle is asynchronous; wait without issuing website fetches.
            deadline = time.monotonic() + 10
            while not worker.evaluate(
                """async()=>{try{
 const [t]=await chrome.tabs.query({url:'https://www.saveticker.com/*'});
 return !!await chrome.tabs.sendMessage(t.id,{type:'api_probe',token:'e'.repeat(32)});
 }catch{return false;}}"""
            ):
                assert time.monotonic() < deadline, "content listener"
                time.sleep(0.1)
            spare_window = None
            if recovery == "repeat":
                spare_window = worker.evaluate("""async()=>{
 const [t]=await chrome.tabs.query({url:'https://www.saveticker.com/*'});
 const before=await chrome.windows.get(t.windowId);
 await chrome.windows.create({tabId:t.id,focused:false});
 return {id:before.id,state:before.state};}""")
            first = worker.evaluate("()=>beginHtmlProbe()")
            if recovery == "delayed":
                deadline = time.monotonic() + 8
                while not first.get("recheckRecovery", {}).get("attempted"):
                    assert time.monotonic() < deadline, first
                    time.sleep(0.2)
                    worker.evaluate("()=>htmlTick()")
                    first = worker.evaluate("()=>chrome.storage.local.get(null)")
            assert (
                first["recheckRecovery"]["attempted"]
                if recovery
                else first["htmlRefreshTrial"]["reloadCount"] == 1
            )
            deadline = time.monotonic() + 12
            while len(html_requests) != 2:
                assert time.monotonic() < deadline, "one normal HTML reload"
                time.sleep(0.1)
            deadline = time.monotonic() + 12
            while True:
                worker.evaluate("()=>htmlTick()")
                state = worker.evaluate("()=>chrome.storage.local.get(null)")
                if (
                    state["recheckRecovery" if recovery else "htmlRefreshTrial"]["pendingAt"]
                    is None
                ):
                    break
                assert time.monotonic() < deadline, state
                time.sleep(0.2)
            worker.evaluate("()=>htmlTick()")
            assert (
                worker.evaluate(
                    """async()=>{
 const [t]=await chrome.tabs.query({url:'https://www.saveticker.com/*'});
 return (await chrome.windows.get(t.windowId)).state;}"""
                )
                == "minimized"
            )
            visibility_deadline = time.monotonic() + 5
            while page.evaluate("document.visibilityState") != "hidden":
                assert time.monotonic() < visibility_deadline, "minimized visibility delivery"
                time.sleep(0.1)
            assert len(html_requests) == 2
            assert len(api_requests) == 1
            if recovery:
                assert state["recheckRecovery"]["reason"] == "recovered"
                assert not state["hostBlocked"]
            else:
                assert state["htmlRefreshTrial"]["reloadCount"] == 1
            assert worker.evaluate("()=>htmlCalls.some(m=>m.type==='api_snapshot')")
            if recovery == "repeat":
                # User/Chrome restoration is corrected only for the exact owned window.
                worker.evaluate("""async()=>{const [t]=await chrome.tabs.query({url:'https://www.saveticker.com/*'});
 await chrome.windows.update(t.windowId,{state:'normal'});await htmlTick();}""")
                window_state = worker.evaluate("()=>chrome.storage.local.get('ownedWindowState')")
                assert window_state["ownedWindowState"] == "minimized"
                assert (
                    worker.evaluate(
                        "async id=>(await chrome.windows.get(id)).state", spare_window["id"]
                    )
                    == spare_window["state"]
                )
                worker.evaluate("""async()=>{
 const s=await chrome.storage.local.get('recheckRecovery');
 const n=Date.now();await chrome.storage.local.set({hostBlocked:true,blockedLanes:['news'],
 blockedAt:n,blockedCause:'cloudflare_challenge',apiHealthySince:n-601001,
 lastApiHealthyAt:n-1000,recheckRecovery:{...s.recheckRecovery,completedAt:n-601002}});
 await htmlTick();}""")
                deadline = time.monotonic() + 12
                while True:
                    worker.evaluate("()=>htmlTick()")
                    state = worker.evaluate("()=>chrome.storage.local.get(null)")
                    if state["recheckRecovery"]["pendingAt"] is None:
                        break
                    assert time.monotonic() < deadline, state
                    time.sleep(0.2)
                assert state["recheckRecovery"]["reloadCount"] == 2
                assert len(html_requests) == 3
                # No ten-minute healthy period after the second recovery: stop.
            # Recurrence/expiry stops without issuing another page request.
            if recovery:
                worker.evaluate("""async()=>{await chrome.storage.local.set({hostBlocked:true,
 blockedLanes:['news']});await htmlTick();}""")
            else:
                worker.evaluate(
                    """async()=>{const s=await chrome.storage.local.get('htmlRefreshTrial');
 await chrome.storage.local.set({htmlRefreshTrial:{...s.htmlRefreshTrial,
 startedAt:Date.now()-2100001}});await htmlTick();}"""
                )
            final = worker.evaluate("()=>chrome.storage.local.get(null)")
            assert final["enabled"] is False
            if recovery:
                assert final["hostBlocked"] is True
                assert final["recheckRecovery"]["reason"] == (
                    "healthy_period_unverified" if recovery == "repeat" else "recovery_used"
                )
            else:
                assert final["htmlRefreshTrial"]["status"] == "finished"
            assert len(html_requests) == (3 if recovery == "repeat" else 2)
        finally:
            if browser is not None:
                browser.close()
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=5)
