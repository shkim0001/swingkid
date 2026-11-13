"""swing 도메인 입출력 스키마 — 포즈 판정 결과."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PoseFinding:
    """포즈 판정 결과."""

    detected: bool
    kind: str                                   # "photo" | "video"
    issue_codes: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)   # 측정 각도(도)
    apex_frame: int | None = None
    reason: str = ""                            # 인식 실패 이유
    series: dict | None = None                  # 영상: 프레임별 왼팔 각도 (화면 그래프용, 판정에는 쓰지 않음)
