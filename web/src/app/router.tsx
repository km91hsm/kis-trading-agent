// 경로 표 — 화면마다 NAV 섹션 컨테이너(id=item.id)를 데이터 전에 바로 그린다. 시스템(4)·오늘(2)·성과(3)·데이터(4)는 페이지(nav.ts pages)가 각자 경로.
import type { ReactElement } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { NAV } from "../generated/nav";
import { Account } from "../screens/accounts/Account.tsx";
import { Accounts } from "../screens/accounts/Accounts.tsx";
import { Data } from "../screens/data/Data.tsx";
import { Decision } from "../screens/decision/Decision.tsx";
import { Performance } from "../screens/performance/Performance.tsx";
import { System } from "../screens/system/System.tsx";
import { Today } from "../screens/today/Today.tsx";
import { Shell } from "./shell/Shell";

export const ROUTES: { path: string; element: ReactElement }[] = [
  ...NAV.today.pages.map((p) => ({ path: p.path, element: <Today page={p.id} /> })),
  { path: NAV.accounts.path, element: <Accounts /> },
  { path: `${NAV.accounts.path}/:id`, element: <Account /> },
  ...NAV.performance.pages.map((p) => ({ path: p.path, element: <Performance page={p.id} /> })),
  ...NAV.data.pages.map((p) => ({ path: p.path, element: <Data page={p.id} /> })),
  ...NAV.system.pages.map((p) => ({ path: p.path, element: <System page={p.id} /> })),
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
