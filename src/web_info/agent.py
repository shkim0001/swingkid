"""web_info 도메인 sub-agent — 웹 검색으로 최신·문서 밖 골프 정보를 찾습니다. supervisor(coach/graph.py)가 부릅니다."""

from __future__ import annotations

from typing import Any, Callable

from src.shared import config
from src.shared.trace import step


class InfoAgent:
    """웹에서 골프 정보를 찾습니다. 골프 관련 질문만 들어옵니다(가드레일이 무관 질문을 먼저 끝냄)."""

    name = config.INFO_AGENT

    def __init__(self, web_search: Callable[[], Any]):
        self._web_search = web_search

    def run(self, state: dict) -> dict:
        question = state["question"]
        searcher = self._web_search()
        results = searcher.search(question)
        contexts = [{"doc_id": r.url, "text": f"{r.title}\n{r.text}".strip()} for r in results]
        error = getattr(searcher, "last_error", None)
        output = [c["doc_id"] for c in contexts] or (f"검색 실패: {error}" if error else "근거 없음")
        return {"trace": step(state, "web_search", question, output), "contexts": contexts}
