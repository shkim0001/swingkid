"""LLM 심사(LLM-as-a-Judge) — 고정 루브릭으로 코치 답변의 품질을 채점합니다.

9일차 4-1 원칙을 그대로 지킵니다.
- 고정 루브릭: 점수별 기준을 상수로 고정하고 "길이가 아니라 정확성으로" 판단하게 합니다.
- 같은 모델·파라미터: judge 모델과 temperature(0)를 상수로 고정합니다. 버전 비교는 항상 같은 judge 로 합니다.
- 재현성 확인: 같은 입력을 두 번 채점해 결과가 같은지 봅니다. 다르면 한 번 더 채점해 다수결로 정하고 사람 검토 대상으로 표시합니다.
- 사람이 최종 확인: 낮은 점수·정답 아님·금지 항목 위반·판정 불일치는 '사람 검토' 목록으로 모읍니다.

정답 문장이 따로 없으므로 평가셋의 expected_traits(기대 특성)를 정답 역할로, forbidden(금지 항목)을 위반 검사로 씁니다.
RAGAS 4대 지표 중 생성기 쪽(Faithfulness·Answer Relevancy)도 여기서 함께 판정합니다(grounded·relevant).

judge 는 생성자에 주입할 수 있어, 테스트는 가짜 judge 로 실제 호출 없이 돌립니다.
"""

from __future__ import annotations

import datetime
from collections import Counter
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from src import config

# judge 모델과 파라미터를 상수로 고정합니다. 버전 비교는 항상 같은 judge 로 합니다.
JUDGE_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
JUDGE_TEMPERATURE = 0
JUDGE_RUNS = 2            # 같은 입력을 두 번 채점해 재현성을 봅니다
CONTEXT_CHARS = 1500      # 근거 한 건당 judge 에게 보여 줄 최대 글자 수
# 근거 개수는 자르지 않습니다: 코치가 본 근거를 judge 도 모두 봐야 '근거 밖 내용'을 잘못 잡지 않습니다
# (평가 v2: TOP_K 를 4 로 올린 뒤 5번째 근거에 있던 연습법을 judge 가 못 봐 위반으로 오판)


class JudgeResult(BaseModel):
    score: int = Field(ge=1, le=5, description="루브릭에 따른 1~5점")
    is_correct: bool = Field(description="기대 특성의 핵심을 의미상 충족하는가")
    relevant: bool = Field(description="질문에 직접 답하는가 (Answer Relevancy)")
    grounded: bool = Field(description="답변의 사실 주장이 모두 근거·측정 결과·이전 대화 안에 있는가 (Faithfulness)")
    missing: list[str] = Field(default_factory=list, description="답변에 빠진 기대 특성")
    violations: list[str] = Field(default_factory=list, description="어긴 금지 항목이나 근거 밖에서 지어낸 내용")
    reasoning: str = Field(description="판단 이유 (2~3문장)")


RUBRIC = """당신은 골프 입문자 코칭 서비스의 답변을 채점하는 심사자입니다.
길이가 아니라 정확성으로 판단하세요. 길고 성실해 보이는 답이라고 점수를 더 주지 않습니다.

점수 기준
5점: 질문에 직접 답하고, 기대 특성을 모두 충족하며, 모든 내용이 근거 안에 있고, 금지 항목이 없다
4점: 핵심은 충족했지만 기대 특성 하나가 약하거나 입문자에게 덜 쉽게 설명했다
3점: 관련은 있지만 기대 특성 일부가 빠졌거나 장황하다
2점: 일부만 맞고 근거 밖 내용이 섞였다
1점: 질문에 답하지 않거나, 사실이 틀리거나, 금지 항목을 어겼다

판정 규칙
- grounded: 답변의 사실·수치 주장이 '근거', '측정 결과', '이전 대화' 안에서 확인되면 true. 인사·격려·일반적인 연결 문장은 따지지 않습니다.
- 근거가 웹 검색 결과이면, 출처에 있는 내용만 말했는지와 조회 기준(날짜)·출처 간 차이를 밝혔는지를 봅니다. 최신 정보의 '정답'은 따로 비교하지 않습니다.
- 측정 결과가 있으면 측정 각도와 교육용 기준값을 제대로 전했는지 봅니다.
- missing 에는 빠진 기대 특성을, violations 에는 어긴 금지 항목이나 근거 밖에서 지어낸 내용을 짧게 적습니다. 없으면 빈 목록입니다."""


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
        parts = [RUBRIC, f"\n오늘 날짜: {datetime.date.today().isoformat()}", f"\n질문: {self.question}"]
        if self.prior:
            parts.append(f"\n이전 대화:\n{self.prior}")
        if self.measured:
            parts.append(f"\n측정 결과(서비스가 계산한 값): {self.measured}")
        parts.append("\n기대 특성:\n" + ("\n".join(f"- {e}" for e in self.expected) or "- (따로 없음: 질문에 근거로 정확히 답하는지만 봅니다)"))
        parts.append("\n금지 항목:\n" + ("\n".join(f"- {f}" for f in self.forbidden) or "- (따로 없음)"))
        ctx = []
        for c in self.contexts:
            text = c.get("text", "")
            ctx.append(f"[{c.get('doc_id', '')}]\n{text[:CONTEXT_CHARS]}{'…' if len(text) > CONTEXT_CHARS else ''}")
        parts.append("\n근거:\n" + ("\n\n".join(ctx) or "(근거 없음)"))
        parts.append(f"\n채점할 답변:\n{self.answer}")
        return "\n".join(parts)


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


def default_judge_llm():
    """실제 judge (Bedrock Claude Sonnet 4.5, temperature 0, 구조화 출력). 함수 안에서만 만듭니다."""
    from langchain_aws import ChatBedrockConverse

    llm = ChatBedrockConverse(model=JUDGE_MODEL, region_name=config.REGION, temperature=JUDGE_TEMPERATURE)
    return llm.with_structured_output(JudgeResult)


class LLMJudge:
    def __init__(self, llm=None, runs: int = JUDGE_RUNS):
        self._llm = llm
        self.runs = runs
        self.calls = 0

    @property
    def llm(self):
        if self._llm is None:
            self._llm = default_judge_llm()
        return self._llm

    def score_once(self, case: JudgeCase) -> JudgeResult:
        self.calls += 1
        result = self.llm.invoke(case.prompt())
        return result if isinstance(result, JudgeResult) else JudgeResult.model_validate(result)

    def judge(self, case: JudgeCase) -> Verdict:
        """두 번 채점해 같으면 그대로, 다르면 한 번 더 채점해 다수결(점수는 중앙값)로 정합니다."""
        runs = [self.score_once(case) for _ in range(self.runs)]
        key = lambda r: (r.score, r.is_correct)  # noqa: E731
        agreed = len({key(r) for r in runs}) == 1
        if not agreed:
            runs.append(self.score_once(case))
        common = Counter(key(r) for r in runs).most_common(1)[0][0]
        final = next(r for r in runs if key(r) == common) if Counter(key(r) for r in runs)[common] > 1 \
            else sorted(runs, key=lambda r: r.score)[len(runs) // 2]
        return Verdict(final=final, runs=runs, agreed=agreed)
