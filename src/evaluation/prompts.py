"""evaluation 도메인 프롬프트 — LLM 심사 고정 루브릭과 심사 프롬프트 조립."""

from __future__ import annotations

import datetime

CONTEXT_CHARS = 1500      # 근거 한 건당 judge 에게 보여 줄 최대 글자 수
# 근거 개수는 자르지 않습니다: 코치가 본 근거를 judge 도 모두 봐야 '근거 밖 내용'을 잘못 잡지 않습니다
# (평가 v2: TOP_K 를 4 로 올린 뒤 5번째 근거에 있던 연습법을 judge 가 못 봐 위반으로 오판)

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


def judge_prompt(case) -> str:
    """JudgeCase 하나를 judge 에게 보낼 프롬프트로 만듭니다."""
    parts = [RUBRIC, f"\n오늘 날짜: {datetime.date.today().isoformat()}", f"\n질문: {case.question}"]
    if case.prior:
        parts.append(f"\n이전 대화:\n{case.prior}")
    if case.measured:
        parts.append(f"\n측정 결과(서비스가 계산한 값): {case.measured}")
    parts.append("\n기대 특성:\n" + ("\n".join(f"- {e}" for e in case.expected) or "- (따로 없음: 질문에 근거로 정확히 답하는지만 봅니다)"))
    parts.append("\n금지 항목:\n" + ("\n".join(f"- {f}" for f in case.forbidden) or "- (따로 없음)"))
    ctx = []
    for c in case.contexts:
        text = c.get("text", "")
        ctx.append(f"[{c.get('doc_id', '')}]\n{text[:CONTEXT_CHARS]}{'…' if len(text) > CONTEXT_CHARS else ''}")
    parts.append("\n근거:\n" + ("\n\n".join(ctx) or "(근거 없음)"))
    parts.append(f"\n채점할 답변:\n{case.answer}")
    return "\n".join(parts)
