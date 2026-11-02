const GAP = 120000, HEALTHY = 600000;
export function recordHealth(previous, now) {
    const last = previous.lastApiHealthyAt, since = previous.apiHealthySince;
    const continuous = Number.isFinite(last) && Number.isFinite(since) && since > 0 && last > 0 && since <= last && last <= now && now - last <= GAP;
    return { lastApiHealthyAt: now, apiHealthySince: continuous ? since : now };
}
export function repeatEligible(state, old, now) {
    const completed = old?.completedAt, since = state.apiHealthySince, last = state.lastApiHealthyAt, blocked = state.blockedAt;
    return state.repeatRecheck === true && old?.reason === 'recovered' && old.pendingAt === null && Number.isFinite(completed) && completed >= 0 && completed <= since && Number.isFinite(since) && Number.isFinite(last) && Number.isFinite(blocked) && since <= last && last <= blocked && blocked <= now && now - blocked <= 60000 && blocked - last <= GAP && last - since >= HEALTHY;
}
