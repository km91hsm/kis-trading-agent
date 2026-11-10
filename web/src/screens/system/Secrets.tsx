// 연결 키 — GET /settings/secrets(토큰 + 이 PC 직접 접속, 이름·설정 여부만)를 마운트·토큰 저장·저장 뒤에 읽고,
// 키마다 비밀번호 입력 → 「저장」 → 확인(바뀌는 키 이름만, 값은 안 보임) → POST /settings/secrets {values}.
// 값은 화면에 되돌려 보이지 않고 저장 뒤 입력을 비운다.
// 403 은 「이 PC 에서만」(quiet403 — 셸 토큰 띠는 띄우지 않는다). SSE 키가 없어 구독하지 않는다.
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Chip } from "../../components/Chip";
import { Confirm } from "../../components/Confirm";
import { Empty } from "../../components/Empty";
import { ApiError, errorText, getAuthed, postJson, tokenStore } from "../../lib/api";
import { secretRows, type SecretRow, type SecretsResp } from "./system.ts";

import "./Secrets.css";

type SaveResp = { updated: string[]; cleared: string[]; backup: string | null; env_overridden: string[]; restart_required: boolean };

const LOCAL_ONLY = "이 PC 에서만 할 수 있습니다(직접 연 화면에서)";
const message = (e: unknown) => (e instanceof ApiError && e.status === 403 ? LOCAL_ONLY : errorText(e));

export function Secrets({ tokenKey }: { tokenKey: number }) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const toggleButton = useRef<HTMLButtonElement>(null);
  const focusAfterSave = useRef(false);
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
        if (ctrl.signal.aborted) return;
        setData(d);
        setLoadError(null);
      },
      (e: unknown) => {
        if (!ctrl.signal.aborted && (e as { name?: string })?.name !== "AbortError") setLoadError(message(e));
      },
    );
    return () => ctrl.abort();
  }, [hasToken, tokenKey, n]);

  useEffect(() => {
    if (!open && !busy && focusAfterSave.current) {
      focusAfterSave.current = false;
      toggleButton.current?.focus();
    }
  }, [open, busy]);

  const rows = data ? secretRows(data) : [];
  const configured = rows.filter((row) => row.set || row.env).length;
  const groups = new Map<string, { title: string; rows: SecretRow[] }>();
  for (const row of rows) {
    const key = JSON.stringify([row.group, row.account]);
    const title = row.account && !row.group.includes(row.account) ? `${row.group} · ${row.account}` : row.group || "기타";
    if (!groups.has(key)) groups.set(key, { title, rows: [] });
    groups.get(key)!.rows.push(row);
  }
  const filled = Object.fromEntries(Object.entries(values).filter(([, v]) => v.trim() !== ""));
  const pending = Object.keys(filled);
  const save = async () => {
    if (busy || !pending.length) return;
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
      focusAfterSave.current = true;
      setOpen(false);
      reload();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };

  const summary = !hasToken ? "설정 변경 인증 필요" : loadError ? "상태 조회 실패" : !data ? "상태 확인 중" : `설정됨 ${configured} · 비어 있음 ${rows.length - configured} · 전체 ${rows.length}개`;
  return (
    <div className="secrets-panel">
      <div className="secrets-panel-header">
        <p className="data-muted">{summary}</p>
        <button ref={toggleButton} type="button" className="btn" aria-expanded={open} aria-controls={panelId} disabled={busy} onClick={() => {
          setOpen(!open);
          setValues({});
          setError(null);
          setConfirming(false);
        }}>{open ? "숨기기" : "연결 키 설정 열기"}</button>
      </div>
      {notice ? <p className="perf-note" role="status">{notice}</p> : null}
      <div id={panelId} hidden={!open}>
        {open && (!hasToken ? <Empty title="설정 변경 인증을 먼저 설정하세요" helpHref="/system/admin#token" helpLabel="설정 변경 인증 열기">연결 키는 설정 변경 인증을 거치고 이 PC 에서 직접 연 화면에서만 봅니다.</Empty>
          : loadError ? <Empty title="연결 키를 불러오지 못했습니다">{loadError}<button type="button" className="btn" onClick={reload}>다시 확인</button></Empty>
          : !data ? <Empty title="불러오는 중" />
          : <form className="sys-secrets" onSubmit={(e) => {
              e.preventDefault();
              if (!busy && pending.length) setConfirming(true);
            }}>
            <p className="secrets-guide data-muted">바꿀 항목에만 새 값을 입력하세요. 빈칸은 기존 값을 유지합니다. 숨기면 저장하지 않은 입력은 지워집니다.</p>
            <div className="secrets-column-labels" aria-hidden="true"><span>연결 항목</span><span>현재 상태</span><span>변경할 값</span></div>
            {Array.from(groups.entries()).map(([key, group]) => (
              <fieldset key={key} className="secrets-group" disabled={busy}>
                <legend><span>{group.title}</span><span className="data-muted">{group.rows.filter((row) => row.set || row.env).length} / {group.rows.length}개 설정</span></legend>
                <ul>
                  {group.rows.map((s) => <li key={s.name} className="secrets-row">
                    <label htmlFor={`${panelId}-${s.name}`} className="secrets-name"><span>{s.label}</span><code>{s.name}</code></label>
                    <span className="secrets-state">{s.env ? <Chip tone="warn">환경변수 우선</Chip> : s.set ? <Chip tone="ok">설정됨</Chip> : <Chip>비어 있음</Chip>}</span>
                    <input id={`${panelId}-${s.name}`} name={s.name} type="password" autoComplete="new-password" placeholder={s.set || s.env ? "새 값으로 교체" : "새 값 입력"}
                      value={values[s.name] ?? ""} onChange={(e) => setValues((v) => ({ ...v, [s.name]: e.target.value }))} />
                  </li>)}
                </ul>
              </fieldset>
            ))}
            {!rows.length ? <Empty title="연결 키 항목 없음" /> : null}
            <div className="secrets-actions"><button type="submit" className="btn btn-primary" disabled={busy || !pending.length}>{busy ? "저장 중…" : "변경 내용 저장"}</button><span className="data-muted" aria-live="polite">{pending.length ? `${pending.length}개 항목 변경 예정` : "변경할 값을 입력하면 저장할 수 있습니다"}</span></div>
            {error ? <p className="data-error" role="alert">{error}</p> : null}
            <Confirm open={confirming} title="연결 키 저장" body={<p>{pending.length}개 키를 바꿉니다: <code>{pending.join(", ")}</code>. 값은 저장 뒤 화면에 보이지 않으며, 이전 값은 백업 파일에 남습니다.</p>}
              onConfirm={() => { setConfirming(false); void save(); }} onClose={() => setConfirming(false)} />
          </form>)}
      </div>
    </div>
  );
}
