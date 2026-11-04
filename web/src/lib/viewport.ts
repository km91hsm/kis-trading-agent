// 뷰포트 폭 훅 — 넓은 화면(기본 1024px 이상)인지. resize 를 구독하고 언마운트 때 푼다.
import { useEffect, useState } from "react";

export function useWide(min = 1024): boolean {
  const [wide, setWide] = useState(() => window.innerWidth >= min);
  useEffect(() => {
    const on = () => setWide(window.innerWidth >= min);
    on();
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, [min]);
  return wide;
}
