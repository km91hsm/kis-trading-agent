// 앱 셸 — 상단 상태 띠 + 설정 변경 인증 안내 + 2단 메뉴 + 화면(<Outlet/>).
// AuthError(401·403·토큰 없음)는 api.ts 의 onAuthError 로 한곳에서 받아 띠 아래 안내를 띄운다(닫기 가능).
import { useEffect, useState } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import { Icon } from "../../components/Icon";
import { AuthError, errorText, onAuthError, onTokenChange, tokenStore } from "../../lib/api";
import { resolveLink } from "../../lib/nav";
import { followHashTarget } from "../../lib/hash-target";
import { Nav } from "./Nav";
import { SavetickerOnboarding } from "./SavetickerOnboarding";
import "../../screens/system/SavetickerStatus.css";
import { StatusBar } from "./StatusBar";
import "../../styles/shell.css";

/** 주소의 #<id> 로 이동할 때마다 그 섹션으로 스크롤한다(하위 목차 클릭·새로 고침).
 *  같은 항목을 다시 눌러도 location.key 는 새로 나오므로 다시 스크롤한다. */
function useHashScroll(): void {
  const { pathname, search, hash, key } = useLocation();
  const navigate = useNavigate();
  useEffect(() => {
    if (!hash) return;
    const resolved = resolveLink(`${pathname}${hash}`);
    if (resolved !== `${pathname}${hash}`) {
      const [path, section] = resolved.split("#");
      navigate({ pathname: path, search, hash: `#${section}` }, { replace: true });
      return;
    }
    const root = document.getElementById("main-content");
    if (root) return followHashTarget(hash, root);
  }, [pathname, search, hash, key, navigate]);
}

export function Shell() {
  const [authNotice, setAuthNotice] = useState<{ error: AuthError; hadToken: boolean } | null>(null);
  useEffect(() => onAuthError((error) => setAuthNotice({ error, hadToken: !!tokenStore.get() })), []);
  useEffect(() => onTokenChange(() => {
    if (tokenStore.get()) setAuthNotice((previous) => previous?.error.status === 403 ? previous : null);
  }), []);
  useHashScroll();

  return (
    <div className="shell">
      <a className="skip-link" href="#main-content" onClick={(e) => {
        e.preventDefault();
        const main = document.getElementById("main-content");
        main?.focus();
        main?.scrollIntoView?.({ block: "start" });
      }}>본문으로 바로가기</a>
      <StatusBar />
      {authNotice ? (
        <div className="auth-notice" role="alert" data-auth-notice>
          <Icon name="warn" />
          <span>
            {authNotice.error.status === 403
              ? <>접속 제한: {errorText(authNotice.error)}. 인증 키를 저장해도 같은 출처·직접 로컬 접속 제한은 해제되지 않습니다. </>
              : authNotice.hadToken
                ? <>저장된 인증 키가 서버에서 거부되었습니다. 현재 서버의 키로 교체하세요. </>
                : <>설정 변경 인증이 필요합니다. 이 브라우저에 현재 서버의 인증 키를 저장하세요. </>}
            <Link to="/system/admin#token">설정·운영 › 연결·설정 › 설정 변경 인증</Link>
          </span>
          <button type="button" className="auth-notice-close" aria-label="안내 닫기" onClick={() => setAuthNotice(null)}>
            <Icon name="x" />
          </button>
        </div>
      ) : null}
      <SavetickerOnboarding />
      <div className="shell-body">
        <Nav />
        <main id="main-content" tabIndex={-1} className="shell-main">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
