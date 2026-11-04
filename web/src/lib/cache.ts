// 옛 `web/src/hooks.ts` 의 useLoad 중복 호출 방지를 모듈 캐시로 분리(hooks.ts 길이 때문에 따로 둔다)
// 같은 path+params 는 DEDUPE_MS 안에 한 번만 부르고, 마지막 사용자가 떠나면 진행 중 요청을 끊는다.
import { getJson } from "./api";

/** 같은 path+params 를 이 시간 안에 다시 부르면 진행 중 요청·받은 값을 나눠 쓴다. */
export const DEDUPE_MS = 1000;

type Entry = { at: number; data: unknown; promise?: Promise<unknown>; ctrl?: AbortController; users: number };
const cache = new Map<string, Entry>();

/** 테스트용: 캐시를 비운다(진행 중 요청은 끊는다). */
export function clearResourceCache(): void {
  for (const e of cache.values()) e.ctrl?.abort();
  cache.clear();
}

function sweep(t: number): void {
  for (const [k, e] of cache) if (!e.promise && e.users <= 0 && t - e.at >= DEDUPE_MS) cache.delete(k);
}

/** 진행 중이면 그 promise 를, DEDUPE_MS 안에 받은 값이 있으면 그 값을 준다(force 면 새로 부른다).
 *  release() 로 마지막 사용자가 떠나면 진행 중 요청을 AbortSignal 로 끊는다. */
export function cachedGet<T>(path: string, params: Record<string, string>, force: boolean): { promise: Promise<T>; release(): void } {
  const key = path + JSON.stringify(params);
  const t = Date.now();
  sweep(t);
  let e = cache.get(key);
  if (e && !e.promise && !force && t - e.at < DEDUPE_MS) return { promise: Promise.resolve(e.data as T), release() {} };
  if (!e || !e.promise) {
    const ctrl = new AbortController();
    const entry: Entry = { at: t, data: undefined, ctrl, users: 0 };
    entry.promise = getJson<T>(path, params, { signal: ctrl.signal }).then(
      (d) => {
        entry.data = d;
        entry.at = Date.now();
        entry.promise = undefined;
        entry.ctrl = undefined;
        return d;
      },
      (err: unknown) => {
        if (cache.get(key) === entry) cache.delete(key); // 오류는 담아 두지 않는다
        throw err;
      },
    );
    cache.set(key, entry);
    e = entry;
  }
  const entry = e;
  entry.users += 1;
  let released = false;
  return {
    promise: entry.promise as Promise<T>,
    release() {
      if (released) return;
      released = true;
      entry.users -= 1;
      if (entry.users <= 0 && entry.promise && cache.get(key) === entry) {
        entry.ctrl?.abort();
        cache.delete(key);
      }
    },
  };
}
