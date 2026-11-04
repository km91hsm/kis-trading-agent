"""웹 '다시 시작' 요청 상수(웹 `routes.py` 가 세우고 데몬 `job.consume_restart` 가 소비). 데몬 잡이
FastAPI 라우터 모듈을 import 하지 않도록 따로 둔다."""

RESTART_FLAG = "saveticker_restart_requested"
EXTENSION_NOTE = "일반 Chrome 확장 수집 모드 — Chrome 확장 아이콘(팝업)에서 재개해 주세요"
