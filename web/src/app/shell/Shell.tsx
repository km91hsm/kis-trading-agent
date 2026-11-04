// 앱 셸 — 상단 상태 띠 + 쓰기 토큰 안내 + 2단 메뉴 + 화면(<Outlet/>).
// AuthError(401·403·토큰 없음)는 api.ts 의 onAuthError 로 한곳에서 받아 띠 아래 안내를 띄운다(닫기 가능).
import { useEffect, useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";
import { Icon } from "../../components/Icon";
import { onAuthError } from "../../lib/api";
import { Nav } from "./Nav";
import { StatusBar } from "./StatusBar";
import "../../styles/shell.css";

/** 주소의 #<id> 가 바뀌면 그 섹션으로 스크롤한다(하위 목차 클릭·새로 고침). */
function useHashScroll(): void {
  const { pathname, hash } = useLocation();
  useEffect(() => {
    if (!hash) return;
    document.getElementById(decodeURIComponent(hash.slice(1)))?.scrollIntoView?.({ block: "start" });
  }, [pathname, hash]);
}

export function Shell() {
  const [authNotice, setAuthNotice] = useState(false);
  useEffect(() => onAuthError(() => setAuthNotice(true)), []);
  useHashScroll();

  return (
    <div className="shell">
      <StatusBar />
      {authNotice ? (
        <div className="auth-notice" role="alert" data-auth-notice>
          <Icon name="warn" />
          <span>
            쓰기 토큰이 필요합니다 → <Link to="/system">시스템</Link>
          </span>
          <button type="button" className="auth-notice-close" aria-label="안내 닫기" onClick={() => setAuthNotice(false)}>
            <Icon name="x" />
          </button>
        </div>
      ) : null}
      <div className="shell-body">
        <Nav />
        <main className="shell-main">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
