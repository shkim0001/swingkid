"""accounts 도메인 입출력 스키마 — 가입·로그인 요청, Google 신원."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field


class AuthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


@dataclass(frozen=True)
class GoogleIdentity:
    """Google ID 토큰에서 꺼낸 신원. sub 는 Google 계정마다 바뀌지 않는 고유 ID 입니다."""

    sub: str
    email: str
