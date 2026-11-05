// 옛 `web/src/events.ts` 이식
// SSE(/api/events) 는 탭마다 열면 브라우저의 호스트당 연결 한도(HTTP/1.1 약 6개)를 먹어
// 탭이 여럿이면 모든 fetch 가 pending 된다 → **리더 탭 하나만** EventSource 를 열고
// BroadcastChannel 로 다른 탭에 스냅샷·연결 상태를 중계한다. 리더는 localStorage 에 하트비트를 남기고,
// 하트비트가 STALE_MS 넘게 멈추면 다른 탭이 승계한다.
// 끊긴 동안은 POLL_FALLBACK_MS 마다 SSE 키 전체를 흘려 구독 화면이 스스로 다시 읽게 한다.
import { NOISY_KEYS, SSE_KEYS, type SseKey } from "../generated/sse-keys";
import { noteServerTime } from "./clock";

/** 서버 `change` 이벤트 본문: SSE 키 → 지문. (실제로 `daemon` 은 `{alive, minute}` 객체 — 값으로 비교한다.) */
export type EventSnapshot = Record<SseKey, string> & { server_time?: string };

export const EVENTS_URL = "/api/events";
export const LEADER_KEY = "trader.events.leader";
export const CHANNEL_NAME = "trader-events";
/** 리더 하트비트 주기(옛 events.ts 값). STALE_MS 의 1/3 이라 두 번 놓쳐도 승계하지 않는다. */
export const HEARTBEAT_MS = 2000;
export const STALE_MS = 6000;
export const POLL_FALLBACK_MS = 30000;

export type LeaderRecord = { id: string; ts: number };

export function parseLeader(raw: string | null): LeaderRecord | null {
  if (!raw) return null;
  try {
    const v = JSON.parse(raw) as Partial<LeaderRecord>;
    if (typeof v.id === "string" && typeof v.ts === "number") return { id: v.id, ts: v.ts };
  } catch {
    /* 깨진 값은 없는 것으로 */
  }
  return null;
}

/** 이 탭이 리더가 될 수 있는가: 기록이 없거나 자기 것이거나 하트비트가 staleMs 넘게 멈췄을 때. */
export function canLead(record: LeaderRecord | null, selfId: string, now: number, staleMs = STALE_MS): boolean {
  if (!record) return true;
  if (record.id === selfId) return true;
  return now - record.ts > staleMs;
}

const KEY_SET = new Set<string>(SSE_KEYS);
const fp = (v: unknown) => (v !== null && typeof v === "object" ? JSON.stringify(v) : v);

/** 값이 바뀐 SSE 키(정렬). prev 가 없으면 next 의 SSE 키 전부. server_time·모르는 키는 뺀다. */
export function changedKeys(prev: EventSnapshot | null, next: EventSnapshot): SseKey[] {
  const keys = new Set<string>([...Object.keys(next), ...(prev ? Object.keys(prev) : [])]);
  const out: SseKey[] = [];
  for (const k of keys) {
    if (!KEY_SET.has(k)) continue;
    const a = prev ? fp((prev as Record<string, unknown>)[k]) : undefined;
    const b = fp((next as Record<string, unknown>)[k]);
    if (!prev || a !== b) out.push(k as SseKey);
  }
  return out.sort();
}

/** 전역 갱신(셸 배지 등)에 쓸 키: 잦게 바뀌는 NOISY_KEYS 를 뺀다. 그 키를 직접 구독한 화면은 그대로 받는다. */
export function significantKeys(keys: SseKey[]): SseKey[] {
  return keys.filter((k) => !NOISY_KEYS.includes(k));
}

export type EventsCallback = (keys: SseKey[], snap: EventSnapshot) => void;
export type EventsClient = {
  subscribe(cb: EventsCallback): () => void;
  status(): { connected: boolean; since: number };
  close(): void;
};

export type EventsDeps = {
  EventSourceCtor?: typeof EventSource;
  /** null 이면 중계 없이 탭마다 직접 연다. 생략하면 BroadcastChannel(CHANNEL_NAME). */
  channel?: BroadcastChannel | null;
  storage?: Storage;
  now?: () => number;
  onServerTime?: (iso: string) => void;
  document?: Document;
  selfId?: string;
};

type Message =
  | { type: "change"; snap: EventSnapshot }
  | { type: "hello" }
  | { type: "leader-gone"; id: string }
  | { type: "status"; connected: boolean };

function defaultChannel(): BroadcastChannel | null {
  try {
    return typeof BroadcastChannel !== "undefined" ? new BroadcastChannel(CHANNEL_NAME) : null;
  } catch {
    return null;
  }
}

function defaultStorage(): Storage | null {
  try {
    return typeof localStorage !== "undefined" ? localStorage : null;
  } catch {
    return null;
  }
}

export function createEventsClient(deps: EventsDeps = {}): EventsClient {
  const ES = deps.EventSourceCtor ?? (typeof EventSource !== "undefined" ? EventSource : null);
  const storage = deps.storage ?? defaultStorage();
  const channel = deps.channel === undefined ? defaultChannel() : deps.channel;
  const relay = channel && storage ? channel : null; // 둘 다 있어야 리더 선출
  const now = deps.now ?? (() => Date.now());
  const onServerTime = deps.onServerTime ?? ((iso: string) => noteServerTime(iso));
  const doc = deps.document ?? (typeof document !== "undefined" ? document : null);
  const selfId = deps.selfId ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;

  const subs = new Set<EventsCallback>();
  let source: EventSource | null = null;
  let leader = false;
  let closed = false;
  let last: EventSnapshot | null = null;
  let connected = false;
  let since = now();
  let fallback: ReturnType<typeof setInterval> | undefined;
  let heartbeat: ReturnType<typeof setInterval> | undefined;

  const emit = (keys: SseKey[], snap: EventSnapshot) => {
    for (const cb of [...subs]) cb(keys, snap);
  };
  const post = (msg: Message) => relay?.postMessage(msg);

  const noteTime = (v: unknown) => {
    const t = (v as { server_time?: unknown } | null)?.server_time;
    if (typeof t === "string") onServerTime(t);
  };

  const applySnap = (snap: EventSnapshot) => {
    noteTime(snap);
    const keys = changedKeys(last, snap);
    last = snap;
    if (keys.length) emit(keys, snap);
  };

  const setConnected = (c: boolean) => {
    if (c === connected) return;
    connected = c;
    since = now();
    if (c) {
      if (fallback !== undefined) clearInterval(fallback);
      fallback = undefined;
    } else startFallback();
  };

  // 끊긴 동안: 30초마다 전체 키를 흘리고, 브라우저가 재연결을 포기한(CLOSED) 소스는 다시 연다
  function startFallback() {
    if (closed || fallback !== undefined) return;
    fallback = setInterval(() => {
      emit([...SSE_KEYS], last ?? ({} as EventSnapshot));
      if (leader && source && source.readyState === 2) reopen();
    }, POLL_FALLBACK_MS);
  }

  const open = () => {
    if (!ES) return;
    const es = new ES(EVENTS_URL);
    es.addEventListener("change", (e) => {
      try {
        const snap = JSON.parse((e as MessageEvent).data) as EventSnapshot;
        applySnap(snap);
        post({ type: "change", snap });
      } catch {
        /* 잘못된 이벤트는 무시 */
      }
    });
    // 서버는 변화가 없으면 이름 있는 ping(event: ping) 을 보낸다 — 시계만 맞춘다
    es.addEventListener("ping", (e) => {
      try {
        noteTime(JSON.parse((e as MessageEvent).data));
      } catch {
        /* 무시 */
      }
    });
    es.onopen = () => {
      setConnected(true);
      post({ type: "status", connected: true });
    };
    es.onerror = () => {
      setConnected(false);
      post({ type: "status", connected: false });
    };
    source = es;
  };
  function reopen() {
    source?.close();
    source = null;
    open();
  }

  const writeLeader = () => {
    try {
      storage?.setItem(LEADER_KEY, JSON.stringify({ id: selfId, ts: now() } satisfies LeaderRecord));
    } catch {
      /* 저장 불가(사생활 모드) → 그래도 리더로 동작 */
    }
  };
  const readLeader = (): LeaderRecord | null => {
    try {
      return parseLeader(storage?.getItem(LEADER_KEY) ?? null);
    } catch {
      return null;
    }
  };
  const becomeLeader = () => {
    if (leader || closed) return;
    leader = true;
    writeLeader();
    open();
  };
  const resign = () => {
    if (!leader) return;
    leader = false;
    source?.close();
    source = null;
    if (readLeader()?.id === selfId) {
      try {
        storage?.removeItem(LEADER_KEY);
      } catch {
        /* 무시 */
      }
    }
    post({ type: "leader-gone", id: selfId });
  };
  /** 리더 하트비트. 숨겨져 타이머가 멈춘 사이 다른 탭이 승계했으면(기록이 남의 것이고 신선) 강등한다 —
   *  덮어쓰면 EventSource 가 둘이 되고 기록을 서로 빼앗는다. */
  const leaderBeat = () => {
    const rec = readLeader();
    if (rec && rec.id !== selfId && !canLead(rec, selfId, now())) {
      leader = false;
      source?.close();
      source = null;
      post({ type: "hello" }); // 새 리더에게 마지막 스냅샷·상태를 받는다
      return;
    }
    writeLeader();
  };
  const tryLead = () => {
    if (canLead(readLeader(), selfId, now())) becomeLeader();
  };

  const onMessage = (e: MessageEvent) => {
    const msg = e.data as Message | null;
    if (!msg || typeof msg !== "object" || closed) return;
    if (msg.type === "change") {
      if (!leader) applySnap(msg.snap);
    } else if (msg.type === "hello") {
      if (!leader) return;
      if (last) post({ type: "change", snap: last });
      post({ type: "status", connected });
    } else if (msg.type === "status") {
      if (!leader) setConnected(msg.connected);
    } else if (msg.type === "leader-gone") {
      if (!leader) tryLead();
    }
  };

  /** 탭이 다시 보일 때: 끊긴 리더는 새로 열고, 리더가 아니면 승계를 확인하고 상태를 다시 묻는다. */
  const reconnect = () => {
    if (closed) return;
    if (!relay) {
      if (!connected) reopen();
      return;
    }
    if (leader) {
      if (!connected) reopen();
      return;
    }
    tryLead();
    if (!leader) post({ type: "hello" });
  };
  const onVisibility = () => {
    if (doc?.visibilityState === "visible") reconnect();
  };

  startFallback(); // 첫 open 전까지는 끊긴 상태
  if (relay) {
    relay.addEventListener("message", onMessage);
    tryLead();
    if (!leader) post({ type: "hello" });
    heartbeat = setInterval(() => (leader ? leaderBeat() : tryLead()), HEARTBEAT_MS);
  } else {
    leader = true; // 중계가 없으면 스스로 연다
    open();
  }
  doc?.addEventListener("visibilitychange", onVisibility);

  return {
    subscribe(cb) {
      subs.add(cb);
      return () => {
        subs.delete(cb);
      };
    },
    status: () => ({ connected, since }),
    close() {
      if (closed) return;
      if (heartbeat !== undefined) clearInterval(heartbeat);
      if (fallback !== undefined) clearInterval(fallback);
      heartbeat = fallback = undefined;
      doc?.removeEventListener("visibilitychange", onVisibility);
      if (relay) resign();
      else {
        source?.close();
        source = null;
      }
      closed = true;
      relay?.removeEventListener("message", onMessage);
      channel?.close();
      subs.clear();
    },
  };
}

let singleton: EventsClient | null = null;

/** 앱 전체가 함께 쓰는 클라이언트(처음 부를 때 브라우저 기본값으로 만든다). */
export function eventsClient(): EventsClient {
  if (!singleton) singleton = createEventsClient();
  return singleton;
}

/** 테스트·셸 정리용: 싱글턴을 바꿔 끼운다(이전 것은 닫는다). null 이면 다음 eventsClient() 가 새로 만든다. */
export function setEventsClient(client: EventsClient | null): void {
  if (singleton && singleton !== client) singleton.close();
  singleton = client;
}
