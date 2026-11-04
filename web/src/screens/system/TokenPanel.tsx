// 쓰기 토큰 — 붙여 넣어 「저장」 하거나 「이 PC 에서 받기」(POST /system/bootstrap-token, 토큰 없이 같은 출처) → tokenStore.
// 토큰 값은 화면에 보이지 않고 「설정됨」 만. 원격(403)이면 「이 PC 에서만」.
import { useState } from "react";
import { Chip } from "../../components/Chip";
import { ApiError, errorText, postPublic, tokenStore } from "../../lib/api";

export function TokenPanel({ onChange }: { onChange(): void }) {
  const [value, setValue] = useState("");
  const [has, setHas] = useState(() => !!tokenStore.get());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const store = (token: string) => {
    tokenStore.set(token);
    setHas(!!tokenStore.get());
    setError(null);
    onChange();
  };

  const fetchLocal = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await postPublic<{ token: string }>("/system/bootstrap-token");
      store(r.token);
    } catch (e) {
      setError(e instanceof ApiError && e.status === 403 ? "이 PC 에서만 받을 수 있습니다" : errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <p>{has ? <Chip tone="ok">설정됨</Chip> : <Chip>없음</Chip>}</p>
      <form
        className="perf-filters"
        onSubmit={(e) => {
          e.preventDefault();
          const t = value.trim();
          if (!t) return;
          store(t);
          setValue("");
        }}
      >
        <input type="password" aria-label="쓰기 토큰" autoComplete="off" placeholder="토큰 붙여 넣기" value={value} onChange={(e) => setValue(e.target.value)} />
        <button type="submit" className="btn btn-primary">
          저장
        </button>
        <button type="button" className="btn" disabled={busy} onClick={() => void fetchLocal()}>
          이 PC 에서 받기
        </button>
      </form>
      {error ? (
        <p className="data-error" role="alert">
          {error}
        </p>
      ) : null}
    </>
  );
}
