"""coach 도메인 실행 로직 — 메인 에이전트 그래프 (supervisor + sub-agent 3개 + coach).

프롬프트는 coach/prompts.py, 상태·API 스키마는 coach/schemas.py, 모델 생성은 shared/llm.py 에 있습니다.

흐름 (LangGraph StateGraph):

    start → guard ─(차단)────────────────→ END   status=blocked
                 ├─(골프와 무관)─────────→ END   status=no_answer (검색·LLM 없음)
                 └─(통과)→ supervisor ─┬→ swing_agent     (사진·영상)            ─┐
                              ▲        ├→ knowledge_agent (골프 지식 문서)        ─┤ 결과를 들고
                              │        └→ info_agent      (웹: 최신·문서 밖 정보) ─┤ supervisor 로 복귀
                              └───────────────────────────────────────────────────┘
                 supervisor 의 다음 결정
                   - 근거(contexts)가 있으면 → coach(LLM 이 답변 작성) → END   status=answered
                   - knowledge_agent 가 근거를 못 찾으면 → info_agent 로 한 번 더 (웹 검색)
                   - 자세 인식 실패 / 웹에서도 근거 없음 → END   status=no_answer (LLM 없음)

라우팅 규칙 (결정적·규칙 기반 — 같은 질문은 항상 같은 경로라 자동 채점이 흔들리지 않습니다)
    1. 사진·영상이 있으면 swing_agent
    2. 최신 결과·순위·시세·장소 추천 질문(knowledge/retriever.needs_fresh_info)이면 info_agent
    3. 그 밖의 골프 질문은 knowledge_agent → 근거 없으면 info_agent

설계 원칙
- 가드레일은 가장 먼저, 규칙 기반으로 실행합니다. 차단되면 검색·포즈·웹·LLM을 하나도 부르지 않습니다.
- sub-agent 는 도구만 쓰고 LLM 을 부르지 않습니다. 답변 문장은 coach 가 한 번만 씁니다.
- 스윙 문제 판정은 swing/pose.py 의 순수 함수가 합니다. LLM은 판정 결과를 받아 설명 문장만 씁니다.
- '근거 없음'과 '자세 인식 실패'는 LLM을 부르지 않고 고정 문구로 끝냅니다(지어내기 방지).
- 모델·검색기·웹 검색기·포즈 추정기·체크포인터는 모두 생성자 인자로 주입할 수 있습니다.
  자동 채점은 가짜 모델 + BM25 + 가짜 웹 검색기 + 관절 좌표 fixture 로 실제 API 호출 없이 돌립니다.
- 같은 thread_id 로 다시 물으면 체크포인터에 남은 대화 이력을 이어서 씁니다.

응답 형식 (API 표준 규약 + 이 서비스의 추가 필드)
    answer, contexts[{doc_id, text}], trace[{step, input, output}]   ← 표준 규약
    status, issue_codes, sources, agents                              ← 추가 필드 (agents: 이번에 부른 sub-agent)
    angle_series                                                      ← 영상 판정의 프레임별 왼팔 각도(화면 그래프용)

기억
- 단기(대화방): thread_id 별 체크포인터. 같은 대화 안의 이력·직전 판정('아까 지적한 …').
- 장기(사용자): user_id 별 Store(src/memory/store.py). 자세 판정 기록(코드·각도)을 대화방을 넘어 쌓고,
  새 판정 때 비교 근거로 주며, '지난번 판정이랑 비교해 줘' 같은 질문은 supervisor 가 장기 기억으로 바로 답하게 합니다.
"""

from __future__ import annotations

import uuid
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from src.coach.prompts import HISTORY_TURNS, SYSTEM_PROMPT, build_prompt
from src.coach.schemas import AgentState
from src.guard.rules import blocked_answer, check
from src.knowledge.agent import KnowledgeAgent
from src.knowledge.retriever import BM25Retriever, load_doc_text, needs_fresh_info
from src.memory.store import MEMORY_DOC_ID, LongTermMemory, describe, is_recall_question
from src.shared import config
from src.shared.llm import default_llm, text_of
from src.shared.trace import step as _step
from src.swing.agent import SwingAgent
from src.training.service import PRACTICE_DOC_ID, is_practice_question
from src.web_info.agent import InfoAgent


def _has_media(state: AgentState) -> bool:
    return bool(state.get("pose") or state.get("image_base64") or state.get("video_base64"))


class GolfCoachAgent:
    """골프 코치 에이전트. ask() 한 번이 POST /query 한 번입니다."""

    def __init__(self, llm=None, retriever=None, pose_estimator=None, checkpointer=None, web_search=None, memory=None):
        self._llm = llm
        self.memory = memory or LongTermMemory()   # 사용자별 장기 기억 (대화방 기억은 checkpointer)
        self._llm_injected = llm is not None  # 주입한 모델(가짜 모델 등)이 있으면 모델 선택을 무시합니다
        self._llms: dict[str, Any] = {}       # 모델 ID별로 한 번만 만들어 재사용
        self.model_fallback = True            # False 면 한도 초과 시 다른 모델로 넘기지 않음(평가 --strict-model)
        self._retriever = retriever
        self._pose_estimator = pose_estimator
        self._web_search = web_search
        # sub-agent 는 도구를 처음 쓸 때 만들도록 '도구를 돌려주는 함수'를 받습니다.
        self.sub_agents = {
            a.name: a
            for a in (
                SwingAgent(lambda: self.pose_estimator),
                KnowledgeAgent(lambda: self.retriever),
                InfoAgent(lambda: self.web_search),
            )
        }
        if checkpointer is None:
            from langgraph.checkpoint.memory import InMemorySaver

            checkpointer = InMemorySaver()
        self.graph = self._build().compile(checkpointer=checkpointer)

    # ── 주입 가능한 부품 (없으면 처음 쓸 때 실제 것을 만듭니다) ──

    @property
    def llm(self):
        if self._llm is None:
            self._llm = default_llm(fallback=self.model_fallback)
        return self._llm

    def llm_for(self, model_id: str | None):
        """이번 요청에 쓸 코칭 모델. 주입한 모델이 있거나 모델을 지정하지 않으면 기본 모델입니다."""
        if self._llm_injected or not model_id or model_id == config.MODEL_ID:
            return self.llm
        if model_id not in self._llms:
            self._llms[model_id] = default_llm(model_id, fallback=self.model_fallback)
        return self._llms[model_id]

    def model_label(self, model_id: str | None, served: str | None = None) -> str:
        """응답에 적을 모델 이름. 주입한 모델이면 그 클래스 이름(예: FakeCoachLLM),
        대체 경로로 다른 모델이 답했으면 실제로 답한 모델입니다."""
        if self._llm_injected:
            return type(self._llm).__name__
        return served or model_id or config.MODEL_ID

    @property
    def retriever(self):
        if self._retriever is None:
            from src.knowledge.retriever import ChromaRetriever, HybridRetriever

            self._retriever = HybridRetriever(semantic=ChromaRetriever(), lexical=BM25Retriever())
        return self._retriever

    @property
    def pose_estimator(self):
        if self._pose_estimator is None:
            from src.swing.estimator import MediaPipeEstimator

            self._pose_estimator = MediaPipeEstimator()
        return self._pose_estimator

    @property
    def web_search(self):
        if self._web_search is None:
            # 라이브 웹 검색은 MCP 도구(src/web_info/mcp_server.py 의 golf_web_search)로 호출합니다.
            from src.web_info.search import McpWebSearch

            self._web_search = McpWebSearch()
        return self._web_search

    # ── 노드 ──

    def _start(self, state: AgentState) -> dict:
        user_id = state.get("user_id")
        return {
            "status": "", "answer": "", "issue_codes": [], "contexts": [], "trace": [],
            "finding": None, "agents": [], "next": "",
            "memory": self.memory.findings(user_id) if user_id else [],
        }

    def _guard(self, state: AgentState) -> dict:
        # 같은 thread 에서 이미 코칭 답변을 주고받았다면 이어지는 질문으로 봅니다(골프와 무관 차단만 완화).
        # 로그인한 사용자의 연습 현황 질문('요즘 연습 어때?')은 골프 단어가 없어도 이 서비스의 훈련 기록을 묻는 것이라
        # 같은 방식으로 '골프와 무관' 판정만 완화합니다(의학·구매·인젝션 차단은 그대로).
        practice_q = state.get("practice") is not None and is_practice_question(state["question"])
        result = check(state["question"], has_media=_has_media(state), in_conversation=bool(state.get("history")) or practice_q)
        trace = _step(state, "guard", state["question"], result.code or ("통과" if result.golf else result.reason))
        if not result.golf:
            # 골프와 무관한 질문: 차단하지 않고, 검색·LLM 없이 no_answer 로 끝냅니다.
            return {
                "trace": trace,
                "status": config.STATUS_NO_ANSWER,
                "issue_codes": [],
                "answer": f"{config.NO_ANSWER_TEXT} {config.OFF_TOPIC_SUFFIX}",
                "contexts": [],
            }
        if not result.blocked:
            return {"trace": trace}
        return {
            "trace": trace,
            "status": config.STATUS_BLOCKED,
            "issue_codes": [result.code],
            "answer": blocked_answer(result.code),
            "contexts": [],
        }

    def _supervisor(self, state: AgentState) -> dict:
        """다음에 부를 sub-agent 를 고르거나, 답변 작성(coach)·종료(end)를 정합니다.

        처음 들어오면 질문 종류로 sub-agent 하나를 고르고, sub-agent 가 돌아오면 결과를 보고 다음 행동을 정합니다.
        sub-agent 로 보낼 때만 trace 에 supervisor 단계를 남깁니다(어느 agent 로, 왜).
        """
        question = state["question"]
        called = state.get("agents", [])

        def route(agent: str, reason: str) -> dict:
            return {
                "next": agent,
                "agents": [*called, agent],
                "trace": _step(state, "supervisor", question, f"{agent} ← {reason}"),
            }

        if not called and not _has_media(state) and state.get("practice") is not None and is_practice_question(question):
            return self._practice(state)
        if not called and not _has_media(state) and state.get("user_id") and is_recall_question(question):
            return self._recall(state)

        if not called:
            if _has_media(state):
                return route(config.SWING_AGENT, "사진·영상 자세 판정")
            if needs_fresh_info(question):
                return route(config.INFO_AGENT, "최신 결과·순위·시세 질문이라 웹 검색")
            return route(config.KNOWLEDGE_AGENT, "골프 지식 질문이라 내부 문서 검색")

        if state.get("status") == config.STATUS_NO_ANSWER:  # 자세 인식 실패: sub-agent 가 이미 끝낼 응답을 만듦
            return {"next": "end"}
        if state.get("contexts"):
            return {"next": "coach"}
        if called[-1] == config.KNOWLEDGE_AGENT and config.INFO_AGENT not in called:
            return route(config.INFO_AGENT, "내부 문서에 근거가 없어 웹 검색")
        # 웹에서도 근거가 없으면 지어내지 않고 끝냅니다.
        return {"next": "end", "status": config.STATUS_NO_ANSWER, "answer": config.NO_ANSWER_TEXT, "contexts": []}

    def _recall(self, state: AgentState) -> dict:
        """'지난번 판정이랑 비교해 줘' 같은 질문: sub-agent 대신 장기 기억을 근거로 바로 답변을 씁니다."""
        records = state.get("memory", [])
        question = state["question"]
        if not records:
            return {
                "next": "end",
                "trace": _step(state, "memory", question, "저장된 판정 기록 없음"),
                "status": config.STATUS_NO_ANSWER,
                "answer": f"{config.NO_ANSWER_TEXT} {config.MEMORY_EMPTY_TEXT}",
                "contexts": [],
            }
        contexts = [{"doc_id": MEMORY_DOC_ID, "text": describe(records)}]
        for kind in dict.fromkeys(r["kind"] for r in records):   # 판정 종류별 교정 문서도 함께
            doc_id = config.MEDIA_SOURCE[kind]
            contexts.append({"doc_id": doc_id, "text": load_doc_text(doc_id)})
        return {
            "next": "coach",
            "trace": _step(state, "memory", question, f"이전 판정 기록 {len(records)}건 사용"),
            "contexts": contexts,
        }

    def _practice(self, state: AgentState) -> dict:
        """'요즘 연습 어때?' 같은 질문: 코드가 집계한 연습 요약(과 판정 기록)을 근거로 답변을 씁니다."""
        question = state["question"]
        if not state.get("practice"):
            return {
                "next": "end",
                "trace": _step(state, "practice", question, "연습 기록 없음"),
                "status": config.STATUS_NO_ANSWER,
                "answer": f"{config.NO_ANSWER_TEXT} {config.PRACTICE_EMPTY_TEXT}",
                "contexts": [],
            }
        contexts = [{"doc_id": PRACTICE_DOC_ID, "text": state["practice"]}]
        if state.get("memory"):   # 연습 효과를 물으면 판정 각도 변화도 함께 볼 수 있게
            contexts.append({"doc_id": MEMORY_DOC_ID, "text": describe(state["memory"])})
        return {
            "next": "coach",
            "trace": _step(state, "practice", question, "연습 요약 사용" + (" + 이전 판정 기록" if state.get("memory") else "")),
            "contexts": contexts,
        }

    def _coach(self, state: AgentState) -> dict:
        prompt = build_prompt(state)
        history = state.get("history", [])[-HISTORY_TURNS:]
        messages = [SystemMessage(SYSTEM_PROMPT)]
        for turn in history:
            messages.append(HumanMessage(turn["content"]) if turn["role"] == "user" else AIMessage(turn["content"]))
        messages.append(HumanMessage(prompt))
        message = self.llm_for(state.get("model")).invoke(messages)
        answer = text_of(message).strip()
        finding = state.get("finding")
        update = {
            "trace": _step(state, "coach", state["question"], answer),
            "status": config.STATUS_ANSWERED,
            "served_model": (getattr(message, "response_metadata", None) or {}).get("model_name"),
            "answer": answer,
            "history": [
                *state.get("history", []),
                {"role": "user", "content": state["question"]},
                {"role": "assistant", "content": answer},
            ],
        }
        if finding:
            update["last_media_kind"] = finding["kind"]
            update["last_issue_codes"] = state.get("issue_codes", [])
            update["last_metrics"] = finding["metrics"]
            if state.get("user_id"):   # 판정 결과(코드·각도)만 장기 기억에 남깁니다
                self.memory.add_finding(state["user_id"], finding["kind"], state.get("issue_codes", []), finding["metrics"])
                saved = _step({"trace": []}, "memory", state["user_id"], "이번 판정 기록 저장")
                update["trace"] = [*update["trace"], *saved]
        return update

    # ── 분기 ──

    @staticmethod
    def _after_guard(state: AgentState) -> str:
        if state.get("status") in (config.STATUS_BLOCKED, config.STATUS_NO_ANSWER):
            return "end"
        return "supervisor"

    @staticmethod
    def _after_supervisor(state: AgentState) -> str:
        return state["next"]

    def _build(self) -> StateGraph:
        g = StateGraph(AgentState)
        g.add_node("start", self._start)
        g.add_node("guard", self._guard)
        g.add_node("supervisor", self._supervisor)
        for name, sub_agent in self.sub_agents.items():
            g.add_node(name, sub_agent.run)
            g.add_edge(name, "supervisor")  # sub-agent 는 결과를 들고 항상 supervisor 로 돌아갑니다
        g.add_node("coach", self._coach)
        g.add_edge(START, "start")
        g.add_edge("start", "guard")
        g.add_conditional_edges("guard", self._after_guard, {"end": END, "supervisor": "supervisor"})
        routes = {name: name for name in self.sub_agents} | {"coach": "coach", "end": END}
        g.add_conditional_edges("supervisor", self._after_supervisor, routes)
        g.add_edge("coach", END)
        return g

    # ── 공개 진입점 ──

    def ask(
        self,
        question: str,
        image_base64: str | None = None,
        video_base64: str | None = None,
        thread_id: str | None = None,
        pose_fixture: dict | None = None,
        model: str | None = None,
        user_id: str | None = None,
        practice: str | None = None,
    ) -> dict:
        """질문 하나를 처리해 응답 dict 를 돌려줍니다.

        pose_fixture 는 테스트·채점 러너만 넣는 값입니다. HTTP API(api/app.py)는 이 필드를 받지 않습니다.
        model 은 코칭 모델 ID 입니다. 허용 목록 검사는 app.py 가 합니다.
        user_id 를 주면 장기 기억(대화방을 넘어 남는 사용자별 판정 기록)을 읽고 씁니다.
        practice 는 로그인한 사용자의 연습 요약(src/training/service.practice_summary, 없으면 '')입니다. 게스트는 None.
        """
        config_ = {"configurable": {"thread_id": thread_id or uuid.uuid4().hex}}
        state = self.graph.invoke(
            {
                "question": question,
                "image_base64": image_base64,
                "video_base64": video_base64,
                "pose": pose_fixture,
                "model": model,
                "served_model": None,   # 이전 턴 값이 체크포인트에 남아 섞이지 않게 매번 비웁니다
                "user_id": user_id,
                "practice": practice,   # 매번 넣어 이전 턴 값이 체크포인트에 남아 섞이지 않게
            },
            config_,
        )
        contexts = [{"doc_id": c["doc_id"], "text": c["text"]} for c in state.get("contexts", [])]
        return {
            "answer": state.get("answer", ""),
            "contexts": contexts,
            "trace": state.get("trace", []),
            "status": state.get("status", ""),
            "issue_codes": state.get("issue_codes", []),
            "sources": list(dict.fromkeys(c["doc_id"] for c in contexts)),
            "agents": state.get("agents", []),
            # 영상 판정이면 프레임별 왼팔 각도(화면 그래프용). 그 밖에는 None
            "angle_series": (state.get("finding") or {}).get("series"),
            "model": self.model_label(model, state.get("served_model")),
        }


def build_agent(mode: str = "offline", llm=None, checkpointer=None, web_search=None, memory=None) -> GolfCoachAgent:
    """실행 모드에 맞는 부품으로 에이전트를 만듭니다.

    offline: 가짜 모델 + BM25 + 가짜 웹 검색기 + (포즈는 fixture 주입 전제). 실제 API·네트워크 호출이 없습니다.
    live   : Bedrock Claude Sonnet 4.5 + Titan/Chroma + MCP 웹 검색 도구(DuckDuckGo) + MediaPipe
             + SqliteSaver(대화방 기억) + SqliteStore(장기 기억, memory.sqlite).
    """
    if mode == "offline":
        from src.shared.fakes import FakeCoachLLM
        from src.web_info.search import FakeWebSearch

        return GolfCoachAgent(
            llm=llm or FakeCoachLLM(),
            retriever=BM25Retriever(),
            checkpointer=checkpointer,
            web_search=web_search or FakeWebSearch(),
            memory=memory,
        )
    if mode == "live":
        if checkpointer is None:
            import sqlite3

            from langgraph.checkpoint.sqlite import SqliteSaver

            conn = sqlite3.connect(config.CHECKPOINT_DB, check_same_thread=False)
            checkpointer = SqliteSaver(conn)
        if memory is None:
            from src.memory.store import build_store

            memory = build_store("live")
        return GolfCoachAgent(llm=llm, checkpointer=checkpointer, web_search=web_search, memory=memory)
    raise ValueError(f"알 수 없는 모드입니다: {mode} (offline 또는 live)")
