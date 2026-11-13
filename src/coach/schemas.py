"""coach 도메인 입출력 스키마 — 그래프 상태(AgentState)와 POST /query 요청·응답 모델."""

from __future__ import annotations

from typing import TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.shared import config


class AgentState(TypedDict, total=False):
    # 이번 요청 입력
    question: str
    image_base64: str | None
    video_base64: str | None
    pose: dict | None          # 테스트·채점이 주입한 관절 좌표(pose_fixture)
    model: str | None          # 이번 요청의 코칭 모델 ID (없으면 기본 모델)
    served_model: str | None   # 실제로 답한 모델 (대체 경로로 다른 모델이 답했을 수 있음)
    user_id: str | None        # 장기 기억 주인. 없으면 장기 기억을 쓰지 않음
    practice: str | None       # 이 사용자의 연습 요약(src/training/service.py 가 집계). None=게스트, ''=기록 없음
    # 이번 요청 결과 (start 노드에서 매번 초기화)
    status: str
    answer: str
    issue_codes: list[str]
    contexts: list[dict]
    trace: list[dict]
    finding: dict | None
    agents: list[str]          # 이번 요청에서 supervisor 가 부른 sub-agent (순서대로)
    next: str                  # supervisor 의 다음 행동 (sub-agent 이름 · coach · end)
    memory: list[dict]         # 장기 기억에서 읽은 이 사용자의 최근 판정 기록 (최신이 앞)
    # 대화가 이어지는 동안 남는 값 (체크포인터에 저장)
    history: list[dict]
    last_media_kind: str | None
    last_issue_codes: list[str]
    last_metrics: dict         # 직전 판정의 측정값 (이어지는 질문의 프롬프트에 다시 넣음)


# ── POST /query ─────────────────────────────────────────────


def allowed_model(value: str | None) -> str | None:
    if value is not None and value not in config.AVAILABLE_MODEL_IDS:
        raise ValueError("사용할 수 없는 모델입니다. GET /models 목록에서 골라 주세요.")
    return value


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    question: str = Field(min_length=1, description="사용자 질의")
    image_base64: str | None = Field(default=None, description="측면(down-the-line) 어드레스 사진")
    video_base64: str | None = Field(default=None, description="정면(face-on) 스윙 영상 짧은 클립 (mp4·webm·mov, 움직이는 GIF 도 가능)")
    thread_id: str | None = Field(default=None, description="같은 값을 보내면 대화가 이어집니다")
    model: str | None = Field(default=None, description="코칭 모델 ID. 없으면 기본 모델(Claude Sonnet 4.5)")

    @field_validator("model")
    @classmethod
    def check_model(cls, value: str | None) -> str | None:
        return allowed_model(value)


class ContextItem(BaseModel):
    doc_id: str
    text: str


class TraceItem(BaseModel):
    step: str
    input: str
    output: str


class QueryResponse(BaseModel):
    answer: str
    contexts: list[ContextItem]
    trace: list[TraceItem]
    status: str
    issue_codes: list[str]
    sources: list[str]
    agents: list[str] = Field(default_factory=list, description="이번 질문에 supervisor 가 부른 sub-agent (순서대로)")
    angle_series: dict | None = Field(
        default=None,
        description="영상 판정일 때 프레임별 왼팔 각도 {metric, t(초 또는 null), values, apex_index, threshold}",
    )
    model: str
    training_proposal: dict | None = Field(
        default=None,
        description="이번 판정으로 만든 2주 연습 플랜 제안 {id, issue_codes, drills, weekdays, start, end, sessions} "
        "(로그인했고 문제 코드가 나왔을 때만, 같은 문제로 진행 중인 제안·플랜이 있으면 null)",
    )


# ── POST /demo/fixture (시연: 저장된 관절 좌표로 /query 와 같은 흐름) ──


class DemoFixtureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    question: str = Field(min_length=1)
    fixture: str = Field(pattern=r"^[a-z_]+$")
    thread_id: str | None = None
    model: str | None = None

    @field_validator("model")
    @classmethod
    def check_model(cls, value: str | None) -> str | None:
        return allowed_model(value)
