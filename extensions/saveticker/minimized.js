import { ownsApiTab } from './core.js';
export async function maintainMinimized({ chrome, state, token, healthyAt, active, store, now = Date.now() }) {
    if (!state.keepMinimized || state.backend !== 'api' || state.hostBlocked || !active() || !Number.isFinite(healthyAt) || healthyAt <= 0 || healthyAt > now || now - healthyAt > 120000) return;
    try {
        const tabs = (await chrome.tabs.query({ url: 'https://www.saveticker.com/*' })).filter(t => ownsApiTab(t, token));
        if (!active() || tabs.length !== 1) return;
        const tab = tabs[0];
        if (!Number.isInteger(tab.windowId) || tab.windowId !== state.apiWindowId || new URL(tab.pendingUrl || tab.url).search) { if (active()) await store({ ownedWindowState: 'unverified' }); return; }
        const win = await chrome.windows.get(tab.windowId);
        if (!active()) return;
        const current = await chrome.tabs.get(tab.id);
        if (!active() || !ownsApiTab(current, token) || current.windowId !== tab.windowId || new URL(current.pendingUrl || current.url).search) return;
        const windowTabs = await chrome.tabs.query({ windowId: tab.windowId });
        if (!active()) return;
        if (windowTabs.length !== 1 || windowTabs[0].id !== tab.id || !ownsApiTab(windowTabs[0], token)) { await store({ ownedWindowState: 'unverified' }); return; }
        if (win.state === 'minimized') {
            await store({ ownedWindowState: 'minimized', ownedWindowId: tab.windowId });
            return;
        }
        if (!['normal', 'maximized', 'fullscreen'].includes(win.state)) return;
        await chrome.windows.update(tab.windowId, { state: 'minimized' });
        if (active()) await store({ ownedWindowState: 'minimized', ownedWindowId: tab.windowId, minimizeAppliedCount: (Number.isInteger(state.minimizeAppliedCount) && state.minimizeAppliedCount >= 0 ? state.minimizeAppliedCount : 0) + 1 });
    } catch {
        if (active()) await store({ ownedWindowState: 'unverified' });
    }
}
