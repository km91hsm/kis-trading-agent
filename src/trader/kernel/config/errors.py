"""설정 파일 읽기·검증 실패."""

from __future__ import annotations


class ConfigError(Exception):
    """사용자에게 보여줄 설정 문제(파일 없음·알 수 없는 키·잘못된 값·구문 오류)."""
