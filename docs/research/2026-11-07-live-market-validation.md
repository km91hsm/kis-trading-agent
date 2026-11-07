# KIS 읽기 전용 시세 확인

2026-11-07 03:23 UTC, 기존 인증과 공유 rate limiter로 시세 GET 14건을 실행했다. 국내 거래대금/상승/하락, 미국 NAS/NYS/AMS 각각 거래대금/상승/하락, SPX/COMP 일별 조회가 모두 rt_cd 성공이었다. 순위는 각5개로 제한했으며 계좌·주문 API와 실제 모델은 호출하지 않았다. 응답 원문은 공개 저장소에 넣지 않았다.

확인된 것은 실제 인증·원천 응답·파싱·시장/거래소 분리다. 12개 순위에서 각각5개의 유효행을 얻었고 SPX/COMP는 동일 일별 행의 값과 날짜를 선택했다. 미국 날짜 전일까지 사용하는 기존 정책 때문에 11월5일 관측이 선택됐다. 수신시각을 체결시각으로 해석하지 않는다.

공식 샘플은 국내 `acml_tr_pbmn`을 누적 거래대금, 미국 `tamt`를 거래대금으로 설명한다. 실응답에서 거래대금/거래량은 해당 시장 가격대와 유사했지만, 이 경험적 비율만으로 단위·배율·세션 범위를 확정하지 않는다. 공식 응답 정의에서 배율과 세션 시각을 확인하지 못했으므로 기존 `turnover_unit=unknown`, `basis=unknown`을 유지한다. 미국 응답의 `zdiv`는 가격 소수점 자리수이며 거래대금 배율로 사용하지 않는다. [국내 공식 필드](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/volume_rank/chk_volume_rank.py), [미국 공식 필드](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/overseas_stock/trade_pbmn/chk_trade_pbmn.py)

현재 휴장시간이고 새 collector의 장중10분 이력이 없으므로 5분 거래대금 증가율의 실측 검증은 아직 완료하지 않았다. 합성 경계값 검증과 실제 장중 관측을 구분한다. 운영 반영 후 정규장에 같은 종목·거래소·통화·세션의 세 관측 경계가 쌓여야 확인할 수 있다. 그 전에는 unavailable이 정상이다.

정확 DXY·WTI 선물의 별도 권한 문제는 이 조회 성공으로 해소되지 않는다. 추가 계약이나 해외선물 시세 호출은 하지 않았다.
