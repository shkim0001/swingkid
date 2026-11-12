"""자동 채점·테스트용 가짜 코칭 모델.

실제 Bedrock 을 부르지 않고, 받은 근거 문서와 판정 결과를 정해진 틀로 요약한 문장을 돌려줍니다.
결과가 항상 같으므로 채점이 흔들리지 않고, 호출 횟수(calls)로 'LLM을 부르면 안 되는 경로'도 확인할 수 있습니다.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage


class FakeCoachLLM:
    """invoke(messages) 만 흉내 내는 아주 작은 가짜 모델."""

    def __init__(self):
        self.calls = 0
        self.last_messages = None

    def invoke(self, messages):
        self.calls += 1
        self.last_messages = messages
        prompt = messages[-1].content if messages else ""
        # 프롬프트 안의 '근거 문서:' 줄과 '판정:' 줄을 그대로 옮겨, 어떤 근거로 답했는지 보이게 합니다.
        lines = [ln for ln in str(prompt).splitlines() if ln.startswith(("근거 문서:", "판정:", "이전 기록:"))]
        summary = " / ".join(lines) if lines else "근거 문서를 바탕으로"
        return AIMessage(content=f"[오프라인 가짜 모델 응답] {summary} 내용을 정리해 안내합니다.")
