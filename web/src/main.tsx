import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles/tokens.css";
import "./styles/base.css";
import App from "./App";
import { applyTheme, prefersDark, resolveTheme, themeStore } from "./lib/theme";

// 첫 렌더 전에 테마를 적용해 깜빡임을 막는다.
applyTheme(resolveTheme(themeStore.get(), prefersDark()));

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
