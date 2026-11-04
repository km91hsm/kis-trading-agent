// 감시목록 — GET /settings/watchlist 를 칩 목록으로 고치고(빼기·추가) 「저장」 → PATCH /settings/watchlist {tickers}(목록 전체 교체).
// 쓰기라 토큰이 필요하다 — 없거나 401/403 이면 오류 줄(api.ts 가 전역 안내도 띄운다).
import { useState } from "react";
import { Chip } from "../../components/Chip";
import { Empty } from "../../components/Empty";
import { errorText, patchJson } from "../../lib/api";
import { useResource } from "../../lib/hooks";
import { body } from "../performance/ResourceView";

type WatchlistResp = { tickers: string[] };

export function Watchlist() {
  const res = useResource<WatchlistResp>("/settings/watchlist", undefined, ["watchlist"]);
  // 고치기 전에는 서버 목록을 그대로 보이고, 고치기 시작하면 draft 를 보인다(저장하면 다시 서버 목록으로).
  const [draft, setDraft] = useState<string[] | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const list = draft ?? res.data?.tickers ?? [];

  const add = () => {
    const t = text.trim().toUpperCase();
    if (!t) return;
    setDraft(list.includes(t) ? list : [...list, t]);
    setText("");
  };
  const remove = (t: string) => setDraft(list.filter((x) => x !== t));
  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await patchJson<WatchlistResp>("/settings/watchlist", { tickers: list });
      setDraft(null);
      res.reload();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return body(res, "감시목록", () => (
    <>
      {list.length ? (
        <ul className="data-watchlist">
          {list.map((t) => (
            <li key={t}>
              <Chip>
                {t}
                <button type="button" className="data-chip-x" aria-label={`${t} 빼기`} onClick={() => remove(t)}>
                  ×
                </button>
              </Chip>
            </li>
          ))}
        </ul>
      ) : (
        <Empty title="감시 종목 없음" />
      )}
      <form
        className="perf-filters"
        onSubmit={(e) => {
          e.preventDefault();
          add();
        }}
      >
        <input aria-label="종목 추가" placeholder="종목 코드" value={text} onChange={(e) => setText(e.target.value)} />
        <button type="submit" className="btn">
          추가
        </button>
        <button type="button" className="btn btn-primary" disabled={busy || draft === null} onClick={() => void save()}>
          저장
        </button>
      </form>
      {error ? (
        <p className="data-error" role="alert">
          {error}
        </p>
      ) : null}
    </>
  ));
}
