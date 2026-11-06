// 연결 키 — GET /settings/secrets(토큰 + 이 PC 직접 접속, 이름·설정 여부만)를 마운트·토큰 저장·저장 뒤에 읽고,
// 키마다 비밀번호 입력 → 「저장」 → 확인(바뀌는 키 이름만, 값은 안 보임) → POST /settings/secrets {values}.
// 값은 화면에 되돌려 보이지 않고 저장 뒤 입력을 비운다.
// 403 은 「이 PC 에서만」(quiet403 — 셸 토큰 띠는 띄우지 않는다). SSE 키가 없어 구독하지 않는다.
import { useCallback, useEffect, useState } from "react";
import { Chip } from "../../components/Chip";
import { Confirm } from "../../components/Confirm";
import { Empty } from "../../components/Empty";
import { ApiError, errorText, getAuthed, postJson, tokenStore } from "../../lib/api";
import { secretRows, type SecretsResp } from "./system.ts";

type SaveResp = { updated: string[]; cleared: string[]; backup: string | null; env_overridden: string[]; restart_required: boolean };

const LOCAL_ONLY = "이 PC 에서만 할 수 있습니다(직접 연 화면에서)";
const message = (e: unknown) => (e instanceof ApiError && e.status === 403 ? LOCAL_ONLY : errorText(e));

export function Secrets({ tokenKey }: { tokenKey: number }) {
  const [data, setData] = useState<SecretsResp | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [n, setN] = useState(0);
  const hasToken = !!tokenStore.get();
  const reload = useCallback(() => setN((x) => x + 1), []);

  useEffect(() => {
    if (!hasToken) return;
    const ctrl = new AbortController();
    getAuthed<SecretsResp>("/settings/secrets", undefined, { signal: ctrl.signal, quiet403: true }).then(
      (d) => {
        setData(d);
        setLoadError(null);
      },
      (e: unknown) => {
        if ((e as { name?: string })?.name !== "AbortError") setLoadError(message(e));
      },
    );
    return () => ctrl.abort();
  }, [hasToken, tokenKey, n]);

  const filled = Object.fromEntries(Object.entries(values).filter(([, v]) => v !== ""));
  const pending = Object.keys(filled);
  const save = async () => {
    if (!pending.length) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const r = await postJson<SaveResp>("/settings/secrets", { values: filled }, { quiet403: true });
      setValues({});
      const parts = [`${r.updated.length}개 저장했습니다`];
      if (r.env_overridden.length) parts.push(`환경변수가 우선합니다: ${r.env_overridden.join(", ")}`);
      if (r.restart_required) parts.push("데몬·웹을 재시작해야 반영됩니다");
      setNotice(parts.join(" · "));
      reload();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };

  if (!hasToken) return <Empty title="쓰기 토큰을 먼저 저장하세요">연결 키는 토큰이 있고 이 PC 에서 직접 연 화면에서만 봅니다.</Empty>;
  if (loadError) return <Empty title="연결 키를 불러오지 못했습니다">{loadError}</Empty>;
  if (!data) return <Empty title="불러오는 중" />;

  return (
    <form
      className="sys-secrets"
      onSubmit={(e) => {
        e.preventDefault();
        if (pending.length) setConfirming(true);
      }}
    >
      <Confirm
        open={confirming}
        title="연결 키 저장"
        body={
          <p>
            {pending.length}개 키를 바꿉니다: <code>{pending.join(", ")}</code>. 값은 저장 뒤 화면에 보이지 않으며, 이전 값은 백업 파일에 남습니다.
          </p>
        }
        onConfirm={() => {
          setConfirming(false);
          void save();
        }}
        onClose={() => setConfirming(false)}
      />
      <ul className="sys-lines">
        {secretRows(data).map((s) => (
          <li key={s.name}>
            <span className="sys-secret-name">
              {s.label} <code>{s.name}</code>
              {s.account ? <span className="data-muted"> · {s.account}</span> : null}
            </span>{" "}
            {s.env ? <Chip tone="warn">환경변수 우선</Chip> : s.set ? <Chip tone="ok">설정됨</Chip> : <Chip>비어 있음</Chip>}{" "}
            <input
              type="password"
              aria-label={s.name}
              autoComplete="new-password"
              placeholder="새 값"
              value={values[s.name] ?? ""}
              onChange={(e) => setValues((v) => ({ ...v, [s.name]: e.target.value }))}
            />
          </li>
        ))}
      </ul>
      <button type="submit" className="btn btn-primary" disabled={busy}>
        저장
      </button>
      {error ? (
        <p className="data-error" role="alert">
          {error}
        </p>
      ) : null}
      {notice ? <p className="perf-note">{notice}</p> : null}
    </form>
  );
}
