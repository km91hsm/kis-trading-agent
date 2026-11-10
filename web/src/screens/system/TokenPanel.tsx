// 설정 변경 인증 — 붙여 넣어 「저장」 하거나 「이 PC 에서 받기」(POST /system/bootstrap-token, 토큰 없이 같은 출처) → tokenStore.
// 인증 키 값은 화면에 보이지 않고 「저장됨」 만. 원격(403)이면 「이 PC 에서만」.
import { useId, useRef, useState } from "react";
import { Chip } from "../../components/Chip";
import { useWriteAuth } from "../../components/WriteAuthNotice";
import { ApiError, errorText, postPublic, tokenStore } from "../../lib/api";
import "./TokenPanel.css";

export function TokenPanel({ onChange }: { onChange(): void }) {
  const [value, setValue] = useState("");
  const has = useWriteAuth();
  const [open, setOpen] = useState(() => !tokenStore.get());
  const panelId = useId();
  const toggleButton = useRef<HTMLButtonElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const store = (token: string) => {
    tokenStore.set(token);
    setError(null);
    if (tokenStore.get() === token) {
      onChange();
      setValue("");
      setOpen(false);
      toggleButton.current?.focus();
    } else {
      setError("브라우저에 인증 키를 저장하지 못했습니다. 브라우저 저장소 설정을 확인하세요.");
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
        <p>이 브라우저의 인증 키 {has ? <Chip>저장됨</Chip> : <Chip>없음</Chip>}</p>
        <button ref={toggleButton} type="button" className="btn" aria-expanded={open} aria-controls={panelId} disabled={busy} onClick={() => {
          setOpen(!open);
          setValue("");
          setError(null);
        }}>{open ? "숨기기" : "설정 변경 인증 열기"}</button>
      </div>
      <p className="data-muted">저장됨은 서버 인증 성공을 뜻하지 않습니다. 키가 잘못되면 변경 요청이 거부됩니다.</p>
      <div id={panelId} hidden={!open}>
      {open ? <>
      <p className="data-muted">설정 변경·작업 요청에 쓰는 인증 키(WEB_TOKEN)는 이 브라우저의 현재 주소에 한 개 저장되며 다시 저장하면 기존 값을 바꿉니다. localhost와 원격 주소는 주소별로 따로 저장됩니다. 브라우저나 접속 주소를 바꾸면 다시 설정해야 합니다.</p>
      <p className="data-muted">「이 PC 에서 받기」는 서버가 실행되는 PC에 직접 로컬 접속했을 때만 현재 서버의 인증 키를 받습니다. 원격 주소에서는 관리자가 제공한 키를 직접 붙여 넣으세요. 키가 있어도 같은 출처·직접 로컬 접속 제한은 유지됩니다.</p>
      <form
        className="perf-filters"
        onSubmit={(e) => {
          e.preventDefault();
          const t = value.trim();
          if (!t) return;
          store(t);
        }}
      >
        <input type="password" aria-label="설정 변경 인증 키" autoComplete="off" placeholder={has ? "교체할 인증 키 붙여 넣기" : "인증 키 붙여 넣기"} value={value} onChange={(e) => setValue(e.target.value)} />
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
