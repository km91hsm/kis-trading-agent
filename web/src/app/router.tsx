// 경로 표 — 화면 컴포넌트는 Task 5~12 가 채운다. 그때까지는 NAV 섹션 컨테이너(id=item.id)만 단 빈 화면.
import type { ReactElement } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { Empty } from "../components/Empty";
import { NAV } from "../generated/nav";
import { Account } from "../screens/accounts/Account.tsx";
import { Accounts } from "../screens/accounts/Accounts.tsx";
import { Decision } from "../screens/decision/Decision.tsx";
import { Performance } from "../screens/performance/Performance.tsx";
import { Today } from "../screens/today/Today.tsx";
import { navGroups, type Screen } from "./shell/Nav";
import { Shell } from "./shell/Shell";

function Placeholder({ screen }: { screen: Screen }) {
  const g = navGroups().find((x) => x.screen === screen)!;
  return (
    <div className="screen" data-screen={screen}>
      <h1 className="screen-title">{g.label}</h1>
      {g.items.map((it) => (
        <section key={it.id} id={it.id} className="screen-section">
          <h2>{it.label}</h2>
          <Empty title="준비 중" />
        </section>
      ))}
    </div>
  );
}

export const ROUTES: { path: string; element: ReactElement }[] = [
  { path: NAV.today.path, element: <Today /> },
  { path: NAV.accounts.path, element: <Accounts /> },
  { path: `${NAV.accounts.path}/:id`, element: <Account /> },
  { path: NAV.performance.path, element: <Performance /> },
  { path: NAV.data.path, element: <Placeholder screen="data" /> },
  { path: NAV.system.path, element: <Placeholder screen="system" /> },
  { path: "/decisions/:asset/:id", element: <Decision /> },
];

export function AppRoutes() {
  return (
    <Routes>
      <Route element={<Shell />}>
        {ROUTES.map((r) => (
          <Route key={r.path} path={r.path} element={r.element} />
        ))}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
