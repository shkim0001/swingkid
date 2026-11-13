"""swing 도메인 sub-agent — 사진·영상 자세 판정. supervisor(coach/graph.py)가 사진·영상이 있을 때 부릅니다.

sub-agent 는 자기 도구 하나만 쓰고, 결과(근거·판정)를 상태에 담아 supervisor 에게 돌려줍니다.
최종 답변 문장은 coach 단계가 LLM 으로 한 번만 씁니다(sub-agent 는 LLM 을 부르지 않음).

| sub-agent        | 맡는 질문                                   | 도구                              | 근거(sources)      |
|------------------|---------------------------------------------|-----------------------------------|--------------------|
| SwingAgent       | 사진·영상 자세 판정                         | 포즈 추정기 + 각도 판정기(pose)   | address/backswing  |
| KnowledgeAgent   | 기술·규칙·용어·코스·장비 등 골프 지식       | 지식 검색기(BM25 / Titan+Chroma)  | 내부 문서 파일명   |
| InfoAgent        | 최신 결과·순위, 내부 문서에 없는 골프 정보  | 웹 검색(MCP 도구 → DuckDuckGo / 가짜 검색기) | 출처 URL           |

trace 단계 이름은 도구 기준(pose · retrieve · web_search)입니다. 어느 agent 로 보냈는지는 supervisor 단계에 남습니다.
"""

from __future__ import annotations

import base64
from typing import Any, Callable

from src.knowledge.retriever import load_doc_text
from src.shared import config
from src.shared.trace import step
from src.swing.pose import analyze_pose


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
                "trace": step(state, "pose", finding.kind, f"인식 실패: {finding.reason}"),
                "status": config.STATUS_NO_ANSWER,
                "issue_codes": [config.POSE_NOT_DETECTED],
                "answer": f"{config.NO_ANSWER_TEXT} {config.POSE_RETRY_TEXT}",
                "contexts": [],
            }
        doc_id = config.MEDIA_SOURCE[finding.kind]
        return {
            "trace": step(state, "pose", finding.kind, {"issue_codes": finding.issue_codes, **finding.metrics}),
            "issue_codes": finding.issue_codes,
            "finding": {
                "kind": finding.kind, "metrics": finding.metrics, "apex_frame": finding.apex_frame,
                "series": finding.series,
            },
            "contexts": [{"doc_id": doc_id, "text": load_doc_text(doc_id)}],
        }
