// 세이브티커 Chrome 확장 설치 — 첫 안내 배너(app/shell/SavetickerOnboarding)와 시스템 › 데이터 수집 카드가 함께 쓴다.
// 설치(확장 복사 + Native host 등록)는 웹이 바로 하고, Chrome 의 「압축해제된 확장 로드」는 Chrome 이 막아 사용자가 한다.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Chip } from "../../components/Chip";
import { errorText, postJson } from "../../lib/api";
import type { SavetickerInstallState } from "./system";

export type InstallResult = { extension: string; next_steps: string[]; replaced: boolean };

/** 설치 성공을 같은 화면의 다른 곳(첫 안내 배너·시스템 카드)에 알리는 창 이벤트. */
export const INSTALLED_EVENT = "saveticker-installed";

/** 다른 곳에서 설치가 끝났는지 — 배너와 카드가 같은 상태를 보이게. */
export function useInstalledElsewhere(): boolean {
  const [done, setDone] = useState(false);
  useEffect(() => {
    const on = () => setDone(true);
    window.addEventListener(INSTALLED_EVENT, on);
    return () => window.removeEventListener(INSTALLED_EVENT, on);
  }, []);
  return done;
}

export function useSavetickerInstall() {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<InstallResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const install = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await postJson<InstallResult>("/system/saveticker/install", null));
      window.dispatchEvent(new Event(INSTALLED_EVENT));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return { busy, result, error, install };
}

function CopyPath({ path }: { path: string }) {
  const [message, setMessage] = useState("");
  const failed = "복사하지 못했습니다 — 경로를 직접 선택해 복사하세요";
  return <span className="st-install-path">
    <code>{path}</code>
    <button type="button" className="btn" onClick={() => {
      setMessage("");
      try {
        const done = navigator.clipboard?.writeText(path);
        if (!done) { setMessage(failed); return; }
        done.then(() => setMessage("복사됨"), () => setMessage(failed));
      } catch { setMessage(failed); }
    }}>경로 복사</button>
    <span className="data-muted" role="status">{message}</span>
  </span>;
}

/** macOS 인지 — Chrome 폴더 선택 창의 경로 붙여 넣기 방법이 OS 마다 다르다. 테스트는 값을 주입한다. */
export function isMacPlatform(nav: { platform?: string; userAgent?: string } = navigator): boolean {
  return /Mac/i.test(nav.platform || "") || /Macintosh|Mac OS X/i.test(nav.userAgent || "");
}

/** 설치 뒤 남은 단계: Chrome 로드(경로 복사) → 로그인 정보(설정) → 팝업에서 수집 시작. */
export function InstallSteps({ result, mac = isMacPlatform() }: { result: InstallResult; mac?: boolean }) {
  return <ol className="st-install-steps">
    <li>확장 복사·Chrome 연결 등록 완료{result.replaced ? " (기존 설치를 바꿈)" : ""}</li>
    <li>Chrome 주소창에 <code>chrome://extensions</code> → 오른쪽 위 <b>개발자 모드</b> 켜기 → <b>압축해제된 확장 프로그램 로드</b> → 아래 폴더 선택 {mac
      ? <>(선택 창에서 <kbd>Cmd</kbd>+<kbd>Shift</kbd>+<kbd>G</kbd> 로 붙여 넣기)</>
      : <>(선택 창의 주소 표시줄에 붙여 넣기)</>}
      <div><CopyPath path={result.extension} /></div></li>
    <li>(선택) 재확인·로그아웃에도 멈추지 않게 하려면 <Link to="/system/admin#secrets">설정 › 연결 키 › 세이브티커</Link> 에 이메일·비밀번호 입력</li>
    <li>Chrome 툴바의 확장 아이콘(팝업)에서 <b>수집 시작</b></li>
  </ol>;
}

/** 시스템 › 데이터 수집의 「확장 설치」 카드. */
export function SavetickerInstallCard({ install }: { install: SavetickerInstallState }) {
  const { busy, result, error, install: run } = useSavetickerInstall();
  const elsewhere = useInstalledElsewhere();
  const registered = install.registered || !!result || elsewhere;
  const local = install.extension_dir !== null;
  const version = install.installed_version
    ? `확장 ${install.installed_version} · ${install.update_needed ? `새 버전 ${install.bundled_version ?? ""} 있음` : "최신"}`
    : "확장 미설치";
  return <section className="st-install" aria-label="확장 설치">
    <p className="sys-head">
      <Chip tone={registered ? "ok" : "muted"}>{registered ? "Chrome 연결 등록됨" : "미등록"}</Chip>
      <span>{version}</span>
    </p>
    {local && install.extension_dir ? <p className="data-muted">확장 폴더: <CopyPath path={install.extension_dir} /></p>
      : <p className="data-muted">이 PC 에서 직접 연 화면에서만 설치할 수 있습니다</p>}
    <div><button type="button" className="btn" disabled={busy || !local} onClick={() => void run()}>{busy ? "설치 중…" : "설치/갱신"}</button></div>
    {error ? <p className="data-error" role="alert">{error}</p> : null}
    {result ? <InstallSteps result={result} /> : null}
  </section>;
}
