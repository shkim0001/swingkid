"""evaluation 도메인 입출력 스키마 — judge 구조화 출력, 채점 문항, 최종 판정."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field


class JudgeResult(BaseModel):
    score: int = Field(ge=1, le=5, description="루브릭에 따른 1~5점")
    is_correct: bool = Field(description="기대 특성의 핵심을 의미상 충족하는가")
    relevant: bool = Field(description="질문에 직접 답하는가 (Answer Relevancy)")
    grounded: bool = Field(description="답변의 사실 주장이 모두 근거·측정 결과·이전 대화 안에 있는가 (Faithfulness)")
    missing: list[str] = Field(default_factory=list, description="답변에 빠진 기대 특성")
    violations: list[str] = Field(default_factory=list, description="어긴 금지 항목이나 근거 밖에서 지어낸 내용")
    reasoning: str = Field(description="판단 이유 (2~3문장)")


@dataclass
class JudgeCase:
    """judge 에게 보낼 한 문항."""

    question: str
    answer: str
    contexts: list[dict] = field(default_factory=list)   # [{doc_id, text}]
    expected: list[str] = field(default_factory=list)    # 기대 특성 (사람이 읽는 문장)
    forbidden: list[str] = field(default_factory=list)   # 금지 항목
    measured: str = ""                                   # 자세 판정 결과(측정 각도 등)
    prior: str = ""                                      # 이어지는 질문이면 이전 대화

    def prompt(self) -> str:
        from src.evaluation.prompts import judge_prompt

        return judge_prompt(self)


@dataclass
class Verdict:
    """한 문항의 최종 판정 (여러 번 채점한 결과를 합친 것)."""

    final: JudgeResult
    runs: list[JudgeResult]
    agreed: bool                                          # 처음 두 번의 점수·정답 판단이 같았나

    @property
    def needs_review(self) -> bool:
        f = self.final
        return f.score <= 3 or not f.is_correct or bool(f.violations) or not f.grounded or not self.agreed

    def to_dict(self) -> dict:
        return {
            **self.final.model_dump(),
            "agreed": self.agreed,
            "runs": [r.score for r in self.runs],
            "needs_review": self.needs_review,
        }
