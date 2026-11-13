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

from collections import Counter

from src.evaluation.schemas import JudgeCase, JudgeResult, Verdict
from src.shared import config

# judge 모델과 파라미터를 상수로 고정합니다. 버전 비교는 항상 같은 judge 로 합니다.
JUDGE_MODEL = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
JUDGE_FALLBACKS = (JUDGE_MODEL, "global.anthropic.claude-sonnet-4-5-20250929-v1:0")  # 같은 모델, 다른 추론 프로필
JUDGE_TEMPERATURE = 0
JUDGE_RUNS = 2            # 같은 입력을 두 번 채점해 재현성을 봅니다


def default_judge_llm(model: str = JUDGE_MODEL):
    """실제 judge (Bedrock Claude Sonnet 4.5, temperature 0, 구조화 출력). 함수 안에서만 만듭니다.

    model: 같은 모델의 다른 추론 프로필(us. ↔ global.)로 바꿀 때만 씁니다(일일 사용량 한도 대비).
    """
    from langchain_aws import ChatBedrockConverse

    from botocore.exceptions import BotoCoreError, ClientError

    # 같은 Sonnet 4.5 의 다른 추론 프로필(us. ↔ global.)로만 넘어갑니다. 회차 간 비교를 위해 judge 모델 자체는 바꾸지 않습니다.
    chain = [model, *(m for m in JUDGE_FALLBACKS if m != model)]
    llms = [ChatBedrockConverse(model=m, region_name=config.REGION, temperature=JUDGE_TEMPERATURE).with_structured_output(JudgeResult)
            for m in chain]
    return llms[0].with_fallbacks(llms[1:], exceptions_to_handle=(ClientError, BotoCoreError))


class LLMJudge:
    def __init__(self, llm=None, runs: int = JUDGE_RUNS, model: str = JUDGE_MODEL):
        self._llm = llm
        self.model = model
        self.runs = runs
        self.calls = 0

    @property
    def llm(self):
        if self._llm is None:
            self._llm = default_judge_llm(self.model)
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
