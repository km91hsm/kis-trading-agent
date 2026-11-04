# quotes

## 책임

- 시세·환율 공개면(tier=service). 지금은 스텁이다: `api.fx_rate(settings, conn, pair="USDKRW") -> dict | None` 가 늘 None 을 돌려준다(M11 Task 9 가 원천·캐시와 웹 자원을 채운다).
- quotes 는 accounts 를 import 하지 않는다(accounts 통합 현황이 `quotes.api.fx_rate` 를 쓰므로 순환 방지).
