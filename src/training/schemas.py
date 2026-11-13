"""training 도메인 입력 스키마 — 훈련 관리 API 요청 본문."""

from __future__ import annotations

import datetime

from pydantic import BaseModel, ConfigDict, Field


class DrillCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(max_length=100)
    unit: str = Field(max_length=4)
    target: int


class LogCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: datetime.date | None = Field(default=None, description="연습한 날(YYYY-MM-DD). 없으면 오늘")
    count: int
    memo: str = Field(default="", max_length=1000)


class FromLibraryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(max_length=64)


class PlanCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    drill_id: str = Field(max_length=64)
    day: datetime.date


class RoutineCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    drill_id: str = Field(max_length=64)
    weekdays: list[int] = Field(max_length=7, description="0=월 ~ 6=일")
    start: datetime.date
    end: datetime.date


class SkipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    day: datetime.date
