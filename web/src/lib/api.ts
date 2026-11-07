// 옛 `web/src/api.ts` 이식
// FastAPI 호출 헬퍼. 호출자는 자원 경로(`/accounts`)만 쓰고 여기서 `/api` 를 붙인다.
// 조회는 토큰 없음, 쓰기(POST·PATCH·DELETE)는 Bearer 토큰. 모든 응답의 X-Server-Time 으로 시계를 보정한다.
import { noteServerTime } from "./clock";

const BASE = "/api";
const TOKEN_KEY = "trader.webToken";
const tokenListeners = new Set<() => void>();

/** 저장 완료 신호만 전달한다. 토큰 값은 이벤트에 싣지 않는다. */
export function onTokenChange(listener: () => void): () => void {
  tokenListeners.add(listener);
  return () => { tokenListeners.delete(listener); };
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** 401(토큰 없음·틀림)·403(이 PC 에서만 등) */
export class AuthError extends ApiError {
  constructor(status: 401 | 403 = 401, message = "쓰기 토큰이 필요합니다") {
    super(status, message);
  }
}

export const tokenStore = {
  get(): string | null {
    try {
      return localStorage.getItem(TOKEN_KEY) || null;
    } catch {
      return null;
    }
  },
  set(token: string): void {
    try {
      if (token) localStorage.setItem(TOKEN_KEY, token);
      else localStorage.removeItem(TOKEN_KEY);
      for (const listener of tokenListeners) {
        try { listener(); } catch { /* 화면 갱신 실패가 저장 결과를 바꾸지 않는다 */ }
      }
    } catch {
      /* 저장 불가 환경이면 저장 없이 진행 */
    }
  },
};

type AuthListener = (e: AuthError) => void;
const authListeners = new Set<AuthListener>();

/** 요청이 401·403 으로 실패하거나 토큰 없이 쓰기를 시도할 때마다 불린다. 해제 함수를 돌려준다. */
export function onAuthError(cb: AuthListener): () => void {
  authListeners.add(cb);
  return () => {
    authListeners.delete(cb);
  };
}

function authFail(e: AuthError): never {
  for (const cb of [...authListeners]) {
    try {
      cb(e);
    } catch {
      /* 리스너 오류 격리 — 요청은 AuthError 로 거부되고 다음 리스너도 불린다 */
    }
  }
  throw e;
}

/** 쓰기 옵션: `token` 은 저장된 토큰 대신 쓸 값(저장 전 확인용), `signal` 은 취소,
 *  `quiet403` 이면 403(이 PC 에서만 등)은 리스너 통지 없이 AuthError(403) 로 거부만 한다(401 은 그대로 통지). */
export type WriteOpts = { token?: string; signal?: AbortSignal; quiet403?: boolean };

function withQuery(path: string, params?: Record<string, string>): string {
  const url = BASE + path;
  if (!params || !Object.keys(params).length) return url;
  return url + (url.includes("?") ? "&" : "?") + new URLSearchParams(params).toString();
}

async function errorOf(res: Response, quiet403 = false): Promise<never> {
  let detail = `${res.status} ${res.statusText}`.trim();
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === "string") detail = body.detail;
    else if (body.detail) detail = JSON.stringify(body.detail);
  } catch {
    /* 본문 없음·JSON 아님 */
  }
  if (res.status === 403 && quiet403) throw new AuthError(403, detail);
  if (res.status === 401 || res.status === 403) authFail(new AuthError(res.status, detail));
  throw new ApiError(res.status, detail);
}

async function send<T>(method: string, url: string, init: RequestInit, quiet403 = false): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, { ...init, method });
  } catch (e) {
    if ((e as { name?: string })?.name === "AbortError") throw e; // 취소는 그대로(undici·jsdom 클래스가 달라 이름으로 본다)
    throw new ApiError(0, "서버에 연결할 수 없습니다");
  }
  const serverTime = res.headers.get("X-Server-Time");
  if (serverTime) noteServerTime(serverTime);
  if (!res.ok) await errorOf(res, quiet403);
  const text = await res.text();
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError(res.status, "응답이 JSON 이 아닙니다"); // 예: SPA HTML 폴백
  }
}

async function write<T>(method: string, path: string, body: unknown, opts: WriteOpts = {}): Promise<T> {
  const token = opts.token || tokenStore.get();
  if (!token) authFail(new AuthError());
  const headers: Record<string, string> = { Accept: "application/json", Authorization: `Bearer ${token}` };
  const hasBody = body !== null && body !== undefined;
  if (hasBody) headers["Content-Type"] = "application/json";
  return send<T>(method, BASE + path, {
    headers,
    body: hasBody ? JSON.stringify(body) : undefined,
    signal: opts.signal,
  }, opts.quiet403);
}

export function getJson<T>(path: string, params?: Record<string, string>, opts: { signal?: AbortSignal } = {}): Promise<T> {
  return send<T>("GET", withQuery(path, params), { headers: { Accept: "application/json" }, signal: opts.signal });
}

export function postJson<T>(path: string, body: unknown, opts?: WriteOpts): Promise<T> {
  return write<T>("POST", path, body, opts);
}

export function patchJson<T>(path: string, body: unknown, opts?: WriteOpts): Promise<T> {
  return write<T>("PATCH", path, body, opts);
}

export function deleteJson<T = void>(path: string, opts?: WriteOpts): Promise<T> {
  return write<T>("DELETE", path, null, opts);
}

/** 토큰 없는 같은 출처 POST(T13-R1) — `ensure-fresh`·`bootstrap-token` 처럼 직접 로컬·Origin 검사만 하는 경로.
 *  Authorization 을 붙이지 않는다(브라우저가 Origin 을 붙인다). */
export function postPublic<T>(path: string, body?: unknown, opts: { signal?: AbortSignal; quiet403?: boolean } = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const hasBody = body !== null && body !== undefined;
  if (hasBody) headers["Content-Type"] = "application/json";
  return send<T>("POST", BASE + path, { headers, body: hasBody ? JSON.stringify(body) : undefined, signal: opts.signal }, opts.quiet403);
}

/** 토큰이 필요한 조회(예: `GET /settings/secrets`). 토큰이 없으면 쓰기와 같이 AuthError. */
export function getAuthed<T>(path: string, params?: Record<string, string>, opts?: WriteOpts): Promise<T> {
  const q = withQuery(path, params).slice(BASE.length);
  return write<T>("GET", q, null, opts);
}

/** CSV 내려받기 링크(`?format=csv`). 조회라 토큰 없음. */
export function csvUrl(path: string, params: Record<string, string> = {}): string {
  return withQuery(path, { ...params, format: "csv" });
}

/** 오류 본문에서 사람이 읽을 문구: detail 이 객체면 그 `error`, 문자열이면 그대로. */
export function errorText(e: unknown): string {
  if (!(e instanceof ApiError)) return String((e as Error)?.message ?? e);
  try {
    const d = JSON.parse(e.message) as { error?: unknown };
    if (d && typeof d.error === "string") return d.error;
  } catch {
    /* 문자열 detail */
  }
  return e.message;
}
