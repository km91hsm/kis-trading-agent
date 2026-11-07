// 「시스템」 화면(Task 13) 테스트 고정값 — 백엔드 응답 모양 그대로(kernel/web/routers/{system,jobs,runs}.py,
// features/accounts/routes.py·regime·saveticker·scalp·fo). 값은 모두 합성(paper-1·real-1·005930).
export type Over = Record<string, unknown>;

export function fixtureStatus(over: Over = {}) {
  return { text: "데몬 동작 중\n일시정지 없음", paused: false, flags: ["account_mode"], ...over };
}

export function fixtureDaemon(over: Over = {}) {
  return {
    alive: true,
    pid: 4242,
    started_at: "2026-11-05T08:00:00+09:00",
    uptime_s: 7200,
    heartbeat_pid: 4242,
    pid_mismatch: false,
    updated_at: "2026-11-05T09:59:30+09:00",
    age_s: 30,
    stale: false,
    stopped_at: null,
    stale_after_s: 180,
    jobs: [
      {
        id: "kr_spot_trade",
        kind: "spot_trade",
        trigger: "cron",
        schedule_key: "spot.KR",
        interval_s: null,
        next_at: "2026-11-05T10:30:00+09:00",
        last_start: "2026-11-05T09:30:00+09:00",
        last_end: "2026-11-05T09:31:00+09:00",
        last_status: "ok",
        last_error: null,
      },
    ],
    ...over,
  };
}

export function fixtureJobs() {
  return {
    kinds: [
      { kind: "spot_trade", label: "현물 매매", lane: "spot", trading: true, params_schema: { type: "object" } },
      { kind: "features_snapshot", label: "지표 스냅샷", lane: "data", trading: false, params_schema: { type: "object" } },
    ],
    jobs: [{ id: "spot.trade.KR", kind: "spot_trade", trigger: "cron", schedule_key: "spot.KR", interval_s: null, next_at: "2026-11-05T10:30:00+09:00" }],
  };
}

const req = (over: Over) => ({
  id: 1,
  kind: "spot_trade",
  params: {},
  status: "queued",
  source: "web",
  requested_at: "2026-11-05T09:50:00+09:00",
  started_at: null,
  finished_at: null,
  progress: null,
  error: null,
  result: null,
  priority: 0,
  label: "현물 매매",
  ...over,
});

export function fixtureRequests() {
  return {
    alive: true,
    active: 1,
    requests: [
      req({ id: 7, status: "running", started_at: "2026-11-05T09:55:00+09:00", params: { market: "KR" } }),
      req({ id: 8, status: "queued", kind: "features_snapshot", label: "지표 스냅샷" }),
      req({ id: 5, status: "error", finished_at: "2026-11-05T09:40:00+09:00", error: "타임아웃" }),
    ],
  };
}

export function fixtureRuns() {
  return [
    { id: 31, kind: "spot_trade", market: "KR", started_at: "2026-11-05T09:30:00+09:00", finished_at: "2026-11-05T09:31:30+09:00", status: "ok", error: null, result_json: null },
    { id: 30, kind: "spot_trade", market: "US", started_at: "2026-11-05T05:00:00+09:00", finished_at: null, status: "error", error: "브로커 오류", result_json: null },
  ];
}

export function fixtureCalls() {
  return {
    calls: [
      {
        id: 101,
        run_id: 31,
        ticker: "005930",
        market: "KR",
        agent: "bull",
        model: "sonnet",
        session_id: "s-1",
        cost_usd: "0.012345",
        duration_ms: 12300,
        status: "ok",
        error: null,
        num_turns: 3,
        created_at: "2026-11-05T09:30:30+09:00",
      },
      { id: 102, run_id: 31, ticker: null, market: "KR", agent: "judge", model: "opus", session_id: null, cost_usd: null, duration_ms: null, status: "error", error: "x", num_turns: null, created_at: "2026-11-05T09:31:00+09:00" },
    ],
    sessions_today: 2,
    cost_today: "0.012345",
  };
}

export function fixtureSettings() {
  return {
    markets: ["KR", "US"],
    risk: null,
    debate_rounds: 2,
    models: { researcher_bull: "sonnet", orchestrator: "opus" },
    web: { bind: "127.0.0.1", port: 8000, tailscale_hosts_count: 2, tailscale_users_count: 1 },
    llm: { budget_usd: 20, daily_session_cap: 40 },
  };
}

export function fixtureSecrets() {
  return {
    keys: [
      { name: "KIS_PAPER_APP_KEY", group: "kis", label: "모의 앱 키", account: "paper-1", set: true, env: false },
      { name: "TELEGRAM_BOT_TOKEN", group: "telegram", label: "텔레그램 봇 토큰", account: null, set: false, env: false },
      { name: "WEB_TOKEN", group: "web", label: "웹 토큰", account: null, set: true, env: true },
    ],
    accounts: [{ id: "paper-1", env: "paper", uses: ["kis"], prefix: "KIS_PAPER" }],
  };
}

export function fixtureControlLog() {
  return [
    { id: 2, at: "2026-11-05T10:00:00+09:00", channel: "web", actor: "ohs", key: "account_mode:real-1", old: "approve", new: "auto" },
    { id: 1, at: "2026-11-05T09:00:00+09:00", channel: "telegram", actor: null, key: "paused", old: null, new: { on: true } },
  ];
}

export function fixtureRegime() {
  return {
    enabled: true,
    max_weight_pct: 0.2,
    leverage: 1,
    bear_min_signals: 2,
    market_session: {},
    min_weight_pct: 0.05,
    instruments: {},
    markets: [{ market: "KR", state: "bull", count: 0, since: "2026-11-01", asof: "2026-11-05", signals: {}, view: null, scorecard: null }],
    killswitch: { on: false, reason: null, source: null, accounts: [], at: null },
  };
}

export function fixtureSaveticker(over: Over = {}) {
  return {
    configured: true,
    service: { state: "collecting", reason: null, at: "2026-11-05T09:00:00+09:00", exit_code: null },
    blocked: false,
    reason_text: null,
    lanes: {},
    last_success_at: "2026-11-05T09:58:00+09:00",
    health: "healthy",
    ...over,
  };
}

export function fixtureScalp() {
  return { enabled: false, judge: false, max_weight_pct: 0.05, max_positions: 2, markets: {} };
}

export function fixtureAccounts() {
  return [
    { id: "paper-1", env: "paper", lane: "spot", markets: ["KR"], mode: "auto", paused: false },
    { id: "real-1", env: "real", lane: "spot", markets: ["KR"], mode: "approve", paused: false },
  ];
}
