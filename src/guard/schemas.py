"""guard 도메인 출력 스키마 — 가드레일 판정 결과."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GuardResult:
    blocked: bool
    code: str | None = None   # guard_ 코드 하나 (차단이 아니면 None)
    reason: str = ""          # 사람이 읽는 차단 이유 (trace에 남깁니다)
    golf: bool = True         # False 면 골프와 무관한 질문. 차단하지 않고 검색 없이 no_answer 로 끝냅니다
