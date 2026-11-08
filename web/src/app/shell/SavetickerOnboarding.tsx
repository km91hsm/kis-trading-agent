// 첫 안내 — 세이브티커 확장이 아직 등록되지 않았고 「안 씀」을 누르지 않았으면 셸 상단에서 설치를 권한다.
// 「나중에」는 이번 브라우저 세션만(sessionStorage), 「안 씀」은 원장 플래그로 다시 묻지 않는다.
import { useEffect, useState } from "react";
import { getJson, postJson } from "../../lib/api";
import { InstallSteps, useInstalledElsewhere, useSavetickerInstall } from "../../screens/system/SavetickerInstall";
import type { SavetickerResp } from "../../screens/system/system";

const LATER = "saveticker-onboarding-later";
function later(): boolean {
  try { return sessionStorage.getItem(LATER) === "1"; } catch { return false; }
}
function rememberLater(): void {
  try { sessionStorage.setItem(LATER, "1"); } catch { /* 저장 못 해도 닫는다 */ }
}

export function SavetickerOnboarding() {
  const [show, setShow] = useState(false);
  const [local, setLocal] = useState(false);
  const { busy, result, error, install } = useSavetickerInstall();
  const elsewhere = useInstalledElsewhere();
  useEffect(() => {
    if (later()) return;
    let alive = true;
    getJson<SavetickerResp>("/system/saveticker").then((s) => {
      if (!alive || !s.install) return;
      setLocal(s.install.extension_dir !== null);
      setShow(!s.install.registered && !s.onboarding?.dismissed);
    }, () => { /* 상태를 못 읽으면 안내하지 않는다 */ });
    return () => { alive = false; };
  }, []);
  // 다른 곳(시스템 카드)에서 설치했으면 닫는다. 배너에서 설치했으면 남은 단계를 계속 보인다.
  if (!show || (elsewhere && !result)) return null;
  const close = () => setShow(false);
  return <section className="st-onboarding" aria-label="세이브티커 수집 안내">
    <div className="st-onboarding-head">
      <strong>세이브티커 뉴스 수집을 쓰시겠어요?</strong>
      <span className="data-muted">Chrome 확장으로 세이브티커 뉴스·리포트·순위·캘린더를 모아 분석 자료로 씁니다.</span>
    </div>
    {result ? <InstallSteps result={result} /> : <div className="st-onboarding-actions">
      <button type="button" className="btn btn-primary" disabled={busy || !local} onClick={() => void install()}>{busy ? "설치 중…" : "설치하기"}</button>
      <button type="button" className="btn" onClick={() => { rememberLater(); close(); }}>나중에</button>
      {/* 「안 씀」 저장이 실패해도(토큰 없음 등) 이번 세션에는 다시 묻지 않는다 */}
      <button type="button" className="btn" onClick={() => { rememberLater(); void postJson("/system/saveticker/onboarding", { dismissed: true }).catch(() => undefined); close(); }}>안 씀</button>
      {!local ? <span className="data-muted">이 PC 에서 직접 연 화면에서만 설치할 수 있습니다</span> : null}
    </div>}
    {error ? <p className="data-error" role="alert">{error}</p> : null}
    {result ? <div><button type="button" className="btn" onClick={close}>닫기</button></div> : null}
  </section>;
}
