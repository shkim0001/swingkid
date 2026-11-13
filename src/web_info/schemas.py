"""web_info 도메인 출력 스키마 — 웹 검색 결과 한 건."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WebResult:
    url: str
    title: str
    text: str
