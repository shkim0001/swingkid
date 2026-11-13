"""knowledge 도메인 sub-agent — 내부 골프 지식 문서 검색(RAG). supervisor(coach/graph.py)가 일반 골프 질문에 부릅니다."""

from __future__ import annotations

from typing import Any, Callable

from src.guard.rules import about_pose, is_follow_up, mentions_golf
from src.knowledge.retriever import load_doc_text
from src.shared import config
from src.shared.trace import step


class KnowledgeAgent:
    """내부 골프 지식 문서를 검색합니다. 근거가 없으면 빈 contexts 를 돌려주고, 다음 행동은 supervisor 가 정합니다."""

    name = config.KNOWLEDGE_AGENT

    def __init__(self, retriever: Callable[[], Any]):
        self._retriever = retriever

    def run(self, state: dict) -> dict:
        question = state["question"]
        contexts = [{"doc_id": c.doc_id, "text": c.text} for c in self._retriever().search(question)]
        # 앞선 사진·영상 판정을 잇는 질문이면 그때의 근거 문서를 먼저 붙입니다.
        #   - '아까 알려준 교정 방법은…'처럼 이어 묻는 표현이 있을 때
        #   - '손목 스냅도 봐줘', '코킹은 괜찮아요?'처럼 신체 부위·스윙 동작·평가 요청일 때 (about_pose)
        #   - 검색 결과는 비었지만 골프 이야기일 때
        # 판정 문서를 맨 앞에 두어, '손목' 같은 단어 때문에 퍼팅 문서만 근거가 되는 일을 막습니다.
        last_kind = state.get("last_media_kind")
        if last_kind and (is_follow_up(question) or about_pose(question) or (not contexts and mentions_golf(question))):
            doc_id = config.MEDIA_SOURCE[last_kind]
            contexts = [c for c in contexts if c["doc_id"] != doc_id]
            contexts.insert(0, {"doc_id": doc_id, "text": load_doc_text(doc_id)})
        return {
            "trace": step(state, "retrieve", question, [c["doc_id"] for c in contexts] or "근거 없음"),
            "contexts": contexts,
        }
