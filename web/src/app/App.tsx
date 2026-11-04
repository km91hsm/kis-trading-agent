// 앱 루트 — 셸과 경로. 라우터(BrowserRouter)는 main.tsx 가, 테스트는 MemoryRouter 가 감싼다.
import { AppRoutes } from "./router";

export default function App() {
  return <AppRoutes />;
}
