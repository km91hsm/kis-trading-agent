import { useSyncExternalStore } from "react";
import { onTokenChange, tokenStore } from "../lib/api";
import "./write-auth-notice.css";

/** 저장 여부만 구독한다. 키 값이나 서버의 인증 성공 여부를 화면 상태로 전달하지 않는다. */
export function useWriteAuth(): boolean {
  return useSyncExternalStore(onTokenChange, () => !!tokenStore.get());
}

export function WriteAuthNotice({ available }: { available: boolean }) {
  if (available) return null;
  return <p className="write-auth-notice">
    설정 변경·작업 요청 전에 이 브라우저의 설정 변경 인증이 필요합니다.{" "}
    <a href="/system/admin#token">설정 변경 인증 열기</a>
    <span>조회는 인증 없이 이용할 수 있습니다.</span>
  </p>;
}
