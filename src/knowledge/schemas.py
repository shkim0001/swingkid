"""knowledge 도메인 출력 스키마 — 검색 결과 조각."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    """검색 결과 한 조각. doc_id는 파일명(예: address.md)입니다."""

    doc_id: str
    text: str
    score: float = 0.0
