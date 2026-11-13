"""sub-agent 3개 — supervisor(agent.py)가 질문마다 골라 부릅니다.

각 sub-agent 는 자기 도구 하나만 쓰고, 결과(근거·판정)를 상태에 담아 supervisor 에게 돌려줍니다.
최종 답변 문장은 supervisor 의 coach 단계가 LLM 으로 한 번만 씁니다(sub-agent 는 LLM 을 부르지 않음).

| sub-agent        | 맡는 질문                                   | 도구                              | 근거(sources)      |
|------------------|---------------------------------------------|-----------------------------------|--------------------|
| SwingAgent       | 사진·영상 자세 판정                         | 포즈 추정기 + 각도 판정기(tools)  | address/backswing  |
| KnowledgeAgent   | 기술·규칙·용어·코스·장비 등 골프 지식       | 지식 검색기(BM25 / Titan+Chroma)  | 내부 문서 파일명   |
| InfoAgent        | 최신 결과·순위, 내부 문서에 없는 골프 정보  | 웹 검색(MCP 도구 → DuckDuckGo / 가짜 검색기) | 출처 URL           |

trace 단계 이름은 도구 기준(pose · retrieve · web_search)입니다. 어느 agent 로 보냈는지는 supervisor 단계에 남습니다.
"""

from __future__ import annotations

import base64
from typing import Any, Callable

from src import config
from src.guard import _is_follow_up, about_pose, mentions_golf
from src.retriever import load_doc_text
from src.tools import analyze_pose


def _short(value: Any, limit: int = 160) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _step(state: dict, step: str, input_: Any, output: Any) -> list[dict]:
    return [*state.get("trace", []), {"step": step, "input": _short(input_), "output": _short(output)}]


class SwingAgent:
    """사진·영상에서 관절 좌표를 뽑고 각도로 문제 코드를 판정합니다. 판정은 순수 함수가 결정적으로 합니다."""

    name = config.SWING_AGENT

    def __init__(self, pose_estimator: Callable[[], Any]):
        self._pose_estimator = pose_estimator  # 처음 쓸 때 만들도록 함수로 받습니다

    def run(self, state: dict) -> dict:
        pose = state.get("pose")
        if pose is None:
            estimator = self._pose_estimator()
            if state.get("video_base64"):
                pose = estimator.estimate_video(base64.b64decode(state["video_base64"]))
            else:
                pose = estimator.estimate_image(base64.b64decode(state["image_base64"]))
        finding = analyze_pose(pose)
        if not finding.detected:
            # 인식 실패는 수치를 지어내지 않고 여기서 끝냅니다(웹 검색으로 넘기지 않음).
            return {
                "trace": _step(state, "pose", finding.kind, f"인식 실패: {finding.reason}"),
                "status": config.STATUS_NO_ANSWER,
                "issue_codes": [config.POSE_NOT_DETECTED],
                "answer": f"{config.NO_ANSWER_TEXT} {config.POSE_RETRY_TEXT}",
                "contexts": [],
            }
        doc_id = config.MEDIA_SOURCE[finding.kind]
        return {
            "trace": _step(state, "pose", finding.kind, {"issue_codes": finding.issue_codes, **finding.metrics}),
            "issue_codes": finding.issue_codes,
            "finding": {
                "kind": finding.kind, "metrics": finding.metrics, "apex_frame": finding.apex_frame,
                "series": finding.series,
            },
            "contexts": [{"doc_id": doc_id, "text": load_doc_text(doc_id)}],
        }


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
        if last_kind and (_is_follow_up(question) or about_pose(question) or (not contexts and mentions_golf(question))):
            doc_id = config.MEDIA_SOURCE[last_kind]
            contexts = [c for c in contexts if c["doc_id"] != doc_id]
            contexts.insert(0, {"doc_id": doc_id, "text": load_doc_text(doc_id)})
        return {
            "trace": _step(state, "retrieve", question, [c["doc_id"] for c in contexts] or "근거 없음"),
            "contexts": contexts,
        }


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
        return {"trace": _step(state, "web_search", question, output), "contexts": contexts}
