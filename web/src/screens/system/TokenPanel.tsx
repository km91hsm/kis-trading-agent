// 쓰기 토큰 — 붙여 넣어 「저장」 하거나 「이 PC 에서 받기」(POST /system/bootstrap-token, 토큰 없이 같은 출처) → tokenStore.
// 토큰 값은 화면에 보이지 않고 「설정됨」 만. 원격(403)이면 「이 PC 에서만」.
import { useId, useRef, useState } from "react";
import { Chip } from "../../components/Chip";
import { ApiError, errorText, postPublic, tokenStore } from "../../lib/api";
import "./TokenPanel.css";

export function TokenPanel({ onChange }: { onChange(): void }) {
  const [value, setValue] = useState("");
  const [has, setHas] = useState(() => !!tokenStore.get());
  const [open, setOpen] = useState(() => !tokenStore.get());
  const panelId = useId();
  const toggleButton = useRef<HTMLButtonElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const store = (token: string) => {
    tokenStore.set(token);
    setHas(!!tokenStore.get());
    setError(null);
    if (tokenStore.get() === token) {
      onChange();
      setValue("");
      setOpen(false);
      toggleButton.current?.focus();
    } else {
      setError("브라우저에 토큰을 저장하지 못했습니다. 브라우저 저장소 설정을 확인하세요.");
    }
  };

  const fetchLocal = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await postPublic<{ token: string }>("/system/bootstrap-token", undefined, { quiet403: true });
      store(r.token);
    } catch (e) {
      setError(e instanceof ApiError && e.status === 403 ? "이 PC 에서만 받을 수 있습니다" : errorText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="token-panel-status">
        <p>이 브라우저 {has ? <Chip tone="ok">설정됨</Chip> : <Chip>없음</Chip>}</p>
        <button ref={toggleButton} type="button" className="btn" aria-expanded={open} aria-controls={panelId} disabled={busy} onClick={() => {
          setOpen(!open);
          setValue("");
          setError(null);
        }}>{open ? "숨기기" : "토큰 설정 열기"}</button>
      </div>
      <div id={panelId} hidden={!open}>
      {open ? <>
      <p className="data-muted">토큰은 이 브라우저에 한 개만 저장되며 다시 저장하면 기존 값을 바꿉니다. 「이 PC 에서 받기」는 현재 서버의 토큰을 저장합니다.</p>
      <form
        className="perf-filters"
        onSubmit={(e) => {
          e.preventDefault();
          const t = value.trim();
          if (!t) return;
          store(t);
        }}
      >
        <input type="password" aria-label="쓰기 토큰" autoComplete="off" placeholder={has ? "교체할 토큰 붙여 넣기" : "토큰 붙여 넣기"} value={value} onChange={(e) => setValue(e.target.value)} />
        <button type="submit" className="btn btn-primary" disabled={busy || !value.trim()}>
          {has ? "교체 저장" : "저장"}
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
      </> : null}
      </div>
    </>
  );
}
