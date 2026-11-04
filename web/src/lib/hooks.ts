// 옛 `web/src/hooks.ts` 이식 (useLoad·useNow·useLiveQuotes·usePolling 을 useResource·useNow·useQuotes·useFoQuotes 로 정리)
// 상태 라이브러리 없이 useState/useEffect + 모듈 단위 fetch 캐시. 화면은 자기가 읽는 SSE 키만 구독해
// 그 키가 바뀔 때만 다시 읽고(끊긴 동안은 sse 의 30초 폴백이 전체 키를 흘린다), 가려진 탭은 미뤘다가 보이면 한 번 읽는다.
import { useCallback, useEffect, useRef, useState } from "react";
import type { SseKey } from "../generated/sse-keys";
import { ApiError, getJson } from "./api";
import { cachedGet } from "./cache";
import { now as clockNow } from "./clock";
import { degradeQuotes, nextDelay, QUOTE_TTL_MS, type FoQuotesResp, type QuotesResp } from "./live";
import { eventsClient } from "./sse";

/** 서버 /quotes·/quotes/fo 한 번에 받는 종목 수. */
export const MAX_PER_CALL = 20;

const isAbort = (e: unknown) => (e as { name?: string } | null)?.name === "AbortError";
const isHidden = () => typeof document !== "undefined" && document.visibilityState === "hidden";

// ── 자원 훅 ──────────────────────────────────────────────────────────────────
export type Resource<T> = { data: T | null; error: ApiError | null; loading: boolean; at: number; reload(): void };

/** 마운트 시 GET path(params) 하고, keys 중 하나가 바뀌면(SSE·30초 폴백) 다시 읽는다. 언마운트 시 요청을 끊는다. */
export function useResource<T>(
  path: string,
  params: Record<string, string> | undefined,
  keys: SseKey[],
  opts: { enabled?: boolean } = {},
): Resource<T> {
  const enabled = opts.enabled ?? true;
  const pkey = JSON.stringify(params ?? {});
  const kkey = keys.join(",");
  const [state, setState] = useState<Omit<Resource<T>, "reload">>({ data: null, error: null, loading: enabled, at: 0 });
  const [n, setN] = useState(0);
  const force = useRef(false);
  const deferred = useRef(false);
  // 구독 키 변화·reload 는 1초 dedupe 를 우회한다(값이 바뀌었다는 신호라 직전 응답은 낡았다).
  // 진행 중 요청은 그대로 나눠 써서 같은 이벤트로 여러 컴포넌트가 재조회해도 1회.
  const refetch = useCallback(() => {
    force.current = true;
    setN((x) => x + 1);
  }, []);

  useEffect(() => {
    if (!enabled) {
      setState({ data: null, error: null, loading: false, at: 0 });
      return;
    }
    let alive = true;
    const req = cachedGet<T>(path, JSON.parse(pkey) as Record<string, string>, force.current);
    force.current = false;
    setState((s) => (s.loading ? s : { ...s, loading: true }));
    req.promise.then(
      (data) => alive && setState({ data, error: null, loading: false, at: Date.now() }),
      (e: unknown) => {
        if (!alive || isAbort(e)) return;
        const error = e instanceof ApiError ? e : new ApiError(0, String((e as Error)?.message ?? e));
        setState((s) => ({ ...s, error, loading: false }));
      },
    );
    return () => {
      alive = false;
      req.release();
    };
  }, [path, pkey, enabled, n]);

  useEffect(() => {
    if (!enabled || !kkey) return;
    const mine = new Set(kkey.split(","));
    const off = eventsClient().subscribe((changed) => {
      if (!changed.some((k) => mine.has(k))) return;
      if (isHidden()) deferred.current = true; // 보이면 한 번에 읽는다
      else refetch();
    });
    const onVis = () => {
      if (!isHidden() && deferred.current) {
        deferred.current = false;
        refetch();
      }
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      off();
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [enabled, kkey, refetch]);

  const reload = refetch;
  return { ...state, reload };
}

/** 서버 보정 시각을 tickMs 마다 준다(시계·'n분 전'). */
export function useNow(tickMs = 1000): Date {
  const [t, setT] = useState(() => clockNow());
  useEffect(() => {
    const id = setInterval(() => setT(clockNow()), tickMs);
    return () => clearInterval(id);
  }, [tickMs]);
  return t;
}

export function useVisible(): boolean {
  const [visible, setVisible] = useState(() => !isHidden());
  useEffect(() => {
    const on = () => setVisible(!isHidden());
    document.addEventListener("visibilitychange", on);
    return () => document.removeEventListener("visibilitychange", on);
  }, []);
  return visible;
}

// ── 시세 폴링 ────────────────────────────────────────────────────────────────
export function chunk<T>(xs: T[], n: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < xs.length; i += n) out.push(xs.slice(i, i + n));
  return out;
}

type Polled = { session: string; quotes: Record<string, { stale: boolean }>; next_poll_ms: number };

const mergeBase = <R extends Polled>(parts: R[]): R => ({
  ...parts[0],
  session: parts.some((p) => p.session === "open") ? "open" : parts[0].session,
  quotes: Object.assign({}, ...parts.map((p) => p.quotes)),
  next_poll_ms: Math.min(...parts.map((p) => p.next_poll_ms)),
});

export function mergeQuotes(parts: QuotesResp[]): QuotesResp {
  return {
    ...mergeBase(parts),
    source: parts.every((p) => p.source === "ws") ? "ws" : "rest",
    stream: parts.some((p) => p.stream),
    hub_yield: parts.find((p) => p.hub_yield)?.hub_yield ?? null,
  };
}

const mergeFo = (parts: FoQuotesResp[]): FoQuotesResp => ({
  ...mergeBase(parts),
  kospi200: parts.find((p) => p.kospi200)?.kospi200 ?? null,
});

/** items 를 MAX_PER_CALL 개씩 나눠 부르고 합친다. 다음 조회는 nextDelay(가려지면 멈춤, 보이면 즉시),
 *  장중 응답이 QUOTE_TTL_MS 안에 새로 오지 않거나 실패하면 시세를 stale 로 내린다. */
function usePolledQuotes<R extends Polled>(
  path: string,
  param: string,
  extra: Record<string, string>,
  items: string[],
  merge: (parts: R[]) => R,
): R | null {
  const [resp, setResp] = useState<R | null>(null);
  const key = Array.from(new Set(items.filter(Boolean))).join(",");
  const ekey = JSON.stringify(extra);
  const mergeRef = useRef(merge);
  mergeRef.current = merge;
  useEffect(() => {
    setResp(null);
    if (!key) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let ttl: ReturnType<typeof setTimeout> | undefined;
    let ctrl: AbortController | null = null;
    const base = JSON.parse(ekey) as Record<string, string>;
    const degrade = () => setResp((prev) => (prev ? degradeQuotes(prev) : prev));
    const schedule = (ms: number | null) => {
      clearTimeout(timer);
      timer = ms === null ? undefined : setTimeout(run, ms);
    };
    function run() {
      timer = undefined;
      if (isHidden()) return; // 보이면 onVis 가 다시 부른다
      ctrl?.abort();
      const c = (ctrl = new AbortController());
      Promise.all(chunk(key.split(","), MAX_PER_CALL).map((part) => getJson<R>(path, { ...base, [param]: part.join(",") }, { signal: c.signal })))
        .then((parts) => {
          if (!alive) return;
          const r = mergeRef.current(parts);
          setResp(r);
          clearTimeout(ttl);
          ttl = r.session === "open" ? setTimeout(degrade, QUOTE_TTL_MS) : undefined;
          schedule(nextDelay(r, isHidden()));
        })
        .catch((e: unknown) => {
          if (!alive || isAbort(e)) return;
          degrade();
          schedule(isHidden() ? null : QUOTE_TTL_MS); // 서버 오류면 천천히
        });
    }
    const onVis = () => {
      if (isHidden()) return;
      schedule(null);
      run();
    };
    run();
    document.addEventListener("visibilitychange", onVis);
    return () => {
      alive = false;
      clearTimeout(timer);
      clearTimeout(ttl);
      ctrl?.abort();
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [path, param, ekey, key]);
  return resp;
}

/** 보이는 종목 시세(GET /quotes). 종목이 없으면 부르지 않는다. */
export function useQuotes(market: "KR" | "US", tickers: string[]): QuotesResp | null {
  return usePolledQuotes<QuotesResp>("/quotes", "tickers", { market }, tickers, mergeQuotes);
}

/** 선물옵션 시세(GET /quotes/fo) + KOSPI200. */
export function useFoQuotes(codes: string[]): FoQuotesResp | null {
  return usePolledQuotes<FoQuotesResp>("/quotes/fo", "codes", {}, codes, mergeFo);
}
