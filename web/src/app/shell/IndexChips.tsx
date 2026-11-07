// 지수·환율 칩 — GET /quotes/indices(KOSPI·KOSDAQ, 장중만 값·`next_poll_ms` 로 다시 읽음, 가려진 탭은 멈춤)와
// GET /quotes/fx(USD/KRW, 하루 캐시, 404 면 숨김). 장 밖이거나 값이 없으면 아무것도 그리지 않는다.
import { useEffect, useState } from "react";
import { ApiError, getJson } from "../../lib/api";
import { useResource } from "../../lib/hooks";

type IndexVal = { price: string; change_pct: number | null; at: string; stale: boolean; source: string };
type IndicesResp = { session: "open" | "closed"; at: string; indices: Record<string, IndexVal>; next_poll_ms: number };
type FxResp = { pair: string; rate: string; as_of: string; source: string; stale?: boolean };

const ORDER = ["KOSPI", "KOSDAQ"];
const RETRY_MS = 60000;
const isHidden = () => typeof document !== "undefined" && document.visibilityState === "hidden";

const fmtIndex = (v: string) => Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const fmtRate = (v: string) => Number(v).toLocaleString("en-US", { maximumFractionDigits: 2 });

function change(pct: number | null): { dir: "up" | "down" | "flat"; text: string } {
  if (pct === null || !Number.isFinite(pct)) return { dir: "flat", text: "" };
  if (pct > 0) return { dir: "up", text: `▲${pct.toFixed(2)}%` };
  if (pct < 0) return { dir: "down", text: `▼${Math.abs(pct).toFixed(2)}%` };
  return { dir: "flat", text: "0.00%" };
}

/** 지수 폴링 — 응답의 next_poll_ms 뒤 다시, 오류면 60초 뒤, 가려지면 멈췄다가 보이면 즉시. 언마운트 때 타이머·요청을 끊는다. */
function useIndices(): IndicesResp | null {
  const [resp, setResp] = useState<IndicesResp | null>(null);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let ctrl: AbortController | null = null;
    const schedule = (ms: number) => {
      clearTimeout(timer);
      timer = setTimeout(run, ms);
    };
    function run() {
      timer = undefined;
      if (isHidden()) return;
      ctrl?.abort();
      const c = (ctrl = new AbortController());
      getJson<IndicesResp>("/quotes/indices", undefined, { signal: c.signal })
        .then((r) => {
          if (!alive) return;
          setResp(r);
          schedule(Math.max(1000, r.next_poll_ms || RETRY_MS));
        })
        .catch((e: unknown) => {
          if (!alive || (e as { name?: string })?.name === "AbortError") return;
          setResp(null);
          if (!(e instanceof ApiError && e.status === 404)) schedule(RETRY_MS);
        });
    }
    const onVis = () => {
      if (!isHidden() && timer === undefined) run();
    };
    run();
    document.addEventListener("visibilitychange", onVis);
    return () => {
      alive = false;
      clearTimeout(timer);
      ctrl?.abort();
      document.removeEventListener("visibilitychange", onVis);
    };
  }, []);
  return resp;
}

export function IndexChips() {
  const idx = useIndices();
  const fx = useResource<FxResp>("/quotes/fx", undefined, []);
  const open = idx?.session === "open";
  const chips = open ? ORDER.filter((k) => idx!.indices[k]).map((k) => ({ key: k, val: idx!.indices[k] })) : [];
  const rate = fx.data && !fx.error ? fx.data : null;
  if (!chips.length && !rate) return null;
  return (
    <div className="index-chips" aria-label="지수·환율">
      {chips.map(({ key, val }) => {
        const c = change(val.change_pct);
        return (
          <span key={key} className="index-chip" data-index-chip={key} data-dir={c.dir} data-stale={val.stale ? "" : undefined}>
            <span className="index-chip-label">{key}</span> <span className="num">{fmtIndex(val.price)}</span>
            {c.text ? <span className="index-chip-change num">{c.text}</span> : null}
            {val.stale ? <span className="index-stale">지연</span> : null}
          </span>
        );
      })}
      {rate ? (
        <span className="index-chip" data-index-chip={rate.pair} data-dir="flat" data-stale={rate.stale ? "" : undefined} title={`${rate.as_of} · ${rate.source}`}>
          <span className="index-chip-label">USD/KRW</span> <span className="num">{fmtRate(rate.rate)}</span>
          {rate.stale ? <span className="index-stale">지연</span> : null}
        </span>
      ) : null}
    </div>
  );
}
