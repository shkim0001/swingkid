"""메인 에이전트 그래프 — 골프 코치 (supervisor + sub-agent 3개).

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
    2. 최신 결과·순위·시세·장소 추천 질문(retriever.needs_fresh_info)이면 info_agent
    3. 그 밖의 골프 질문은 knowledge_agent → 근거 없으면 info_agent

설계 원칙
- 가드레일은 가장 먼저, 규칙 기반으로 실행합니다. 차단되면 검색·포즈·웹·LLM을 하나도 부르지 않습니다.
- sub-agent 는 도구만 쓰고 LLM 을 부르지 않습니다. 답변 문장은 coach 가 한 번만 씁니다.
- 스윙 문제 판정은 tools.py 의 순수 함수가 합니다. LLM은 판정 결과를 받아 설명 문장만 씁니다.
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
- 장기(사용자): user_id 별 Store(src/memory.py). 자세 판정 기록(코드·각도)을 대화방을 넘어 쌓고,
  새 판정 때 비교 근거로 주며, '지난번 판정이랑 비교해 줘' 같은 질문은 supervisor 가 장기 기억으로 바로 답하게 합니다.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from src import config
from src.guard import blocked_answer, check
from src.memory import MEMORY_DOC_ID, LongTermMemory, describe, is_recall_question
from src.training import PRACTICE_DOC_ID, is_practice_question
from src.retriever import BM25Retriever, load_doc_text, needs_fresh_info
from src.subagents import InfoAgent, KnowledgeAgent, SwingAgent, _step

HISTORY_TURNS = 6  # 코칭 프롬프트에 넣을 최근 대화 수(질문+답변 쌍 기준 3턴)

SYSTEM_PROMPT = """당신은 골프를 시작한 지 0~3개월 된 입문자를 돕는 골프 코치입니다.
규칙:
1. 정중한 존댓말로, 입문자가 이해할 수 있는 쉬운 말로 답합니다.
2. 반드시 아래 '근거 문서' 안의 내용만 사용합니다. 문서에 없는 내용은 지어내지 않습니다.
3. '판정'이 있으면 측정 각도와 교육용 기준값을 먼저 알려 주고, 근거 문서의 교정 방법과 연습 방법을 안내합니다.
4. 장비 구매 추천, 가격, 의학적 진단은 하지 않습니다.
   이 질문은 시스템이 이미 답해도 되는 범위로 판정했으므로, 근거 문서가 있으면 범위를 이유로 거절하지 않습니다.
   제품명·브랜드명은 쓰지 않고, 근거에 있는 일반 특징(예: SPF, 워터프루프)만으로 답합니다.
   근거에 없는 조건별 조언(피부 타입·체형별 등)을 덧붙이지 않습니다.
5. 근거가 '웹 검색 결과'이면 조회 날짜 기준의 정보라고 밝히고, 결과끼리 말이 다르면 단정하지 말고 그렇다고 알립니다.
   근거가 골프 규칙 문서이면 몇 년판 규칙 기준인지(예: 2023년판 골프 규칙 기준) 답변에 밝힙니다.
6. '이전 기록'(이 사용자의 지난 자세 판정)이 있으면 이번 판정과 비교해 좋아진 점이나 반복되는 문제를 한 문장으로 짚습니다.
   기록에 없는 날짜·수치는 만들지 않습니다.
7. '연습 기록'(이 사용자의 훈련 기록, 코드가 집계)이 있으면 그 수치만 사용해 연습 상황을 짚고, 기록에 없는 연습·날짜는 만들지 않습니다.
8. 답변은 5문장 이내로 짧게 씁니다."""

METRIC_LABELS = {
    "spine_tilt": ("척추 앞기울기", config.SPINE_TILT_RANGE),
    "knee_flex": ("무릎 각도", config.KNEE_FLEX_RANGE),
    "lead_arm": ("백스윙 정점 왼팔 각도", (config.LEAD_ARM_MIN, None)),   # 상한 없음: 문서 표현 그대로 '160° 이상'
}


def _standard(lo: float, hi: float | None) -> str:
    """교육용 기준값을 근거 문서와 같은 표현으로 (예: 30~45°, 160° 이상)."""
    return f"{lo:g}° 이상" if hi is None else f"{lo:g}~{hi:g}°"


class AgentState(TypedDict, total=False):
    # 이번 요청 입력
    question: str
    image_base64: str | None
    video_base64: str | None
    pose: dict | None          # 테스트·채점이 주입한 관절 좌표(pose_fixture)
    model: str | None          # 이번 요청의 코칭 모델 ID (없으면 기본 모델)
    served_model: str | None   # 실제로 답한 모델 (대체 경로로 다른 모델이 답했을 수 있음)
    user_id: str | None        # 장기 기억 주인. 없으면 장기 기억을 쓰지 않음
    practice: str | None       # 이 사용자의 연습 요약(src/training.py 가 집계). None=게스트, ''=기록 없음
    # 이번 요청 결과 (start 노드에서 매번 초기화)
    status: str
    answer: str
    issue_codes: list[str]
    contexts: list[dict]
    trace: list[dict]
    finding: dict | None
    agents: list[str]          # 이번 요청에서 supervisor 가 부른 sub-agent (순서대로)
    next: str                  # supervisor 의 다음 행동 (sub-agent 이름 · coach · end)
    memory: list[dict]         # 장기 기억에서 읽은 이 사용자의 최근 판정 기록 (최신이 앞)
    # 대화가 이어지는 동안 남는 값 (체크포인터에 저장)
    history: list[dict]
    last_media_kind: str | None
    last_issue_codes: list[str]
    last_metrics: dict         # 직전 판정의 측정값 (이어지는 질문의 프롬프트에 다시 넣음)


MEDIA_LABELS = {"photo": "측면 어드레스 사진", "video": "정면 스윙 영상"}


def _last_finding_line(state: AgentState) -> str:
    """'직전 판정(같은 대화)' 한 줄: 종류 · 문제 코드 · 측정값 · 측정한 항목."""
    kind = state["last_media_kind"]
    codes = state.get("last_issue_codes") or ["문제 없음"]
    measured, labels = [], []
    for key, value in state["last_metrics"].items():
        label, (lo, hi) = METRIC_LABELS[key]
        labels.append(label)
        measured.append(f"{label} {value}° (교육용 기준 {_standard(lo, hi)})")
    return (
        f"직전 판정(같은 대화, {MEDIA_LABELS.get(kind, kind)}): {', '.join(codes)} | " + ", ".join(measured)
        + f"\n측정한 항목은 {', '.join(labels)}뿐입니다. 그 밖의 부위는 측정값이 없으니 수치를 만들지 말고 근거 문서의 설명과 점검 방법으로 안내합니다."
    )


def _has_media(state: AgentState) -> bool:
    return bool(state.get("pose") or state.get("image_base64") or state.get("video_base64"))


def _is_web(doc_id: str) -> bool:
    return doc_id.startswith(("http://", "https://"))


def _text_of(message) -> str:
    """ChatBedrockConverse 는 content 를 블록 목록으로 주기도 하므로 텍스트만 모읍니다."""
    content = getattr(message, "content", message)
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content)


def fallback_chain(model_id: str | None) -> list[str]:
    """코칭 모델을 시도할 순서: 고른 모델(없으면 기본 모델) → 허용 목록(config.AVAILABLE_MODELS)의 나머지 순서대로."""
    first = model_id or config.MODEL_ID
    return [first, *(m["id"] for m in config.AVAILABLE_MODELS if m["id"] != first)]


def default_llm(model_id: str = config.MODEL_ID, fallback: bool = True):
    """실제 코칭 모델. 기본값은 고정 스택이고, 웹 화면에서 고른 모델이면 그 ID로 만듭니다. 함수 안에서만 만듭니다.

    고른 모델이 사용량 한도(ThrottlingException)·권한·일시 장애 등 Bedrock 오류로 실패하면
    허용 목록의 다음 모델로 넘어갑니다(LangChain with_fallbacks). 실제로 답한 모델은 응답의
    response_metadata["model_name"] 에 남아 API 응답의 model 필드로 나갑니다.
    fallback=False 면 고른 모델만 씁니다(평가 --strict-model: 회차 간 같은 모델 조건을 지키기 위해).
    """
    from botocore.exceptions import BotoCoreError, ClientError
    from langchain_aws import ChatBedrockConverse

    llms = [ChatBedrockConverse(model=m, region_name=config.REGION, temperature=0) for m in fallback_chain(model_id)]
    if not fallback:
        return llms[0]
    return llms[0].with_fallbacks(llms[1:], exceptions_to_handle=(ClientError, BotoCoreError))


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
            from src.retriever import ChromaRetriever, HybridRetriever

            self._retriever = HybridRetriever(semantic=ChromaRetriever(), lexical=BM25Retriever())
        return self._retriever

    @property
    def pose_estimator(self):
        if self._pose_estimator is None:
            from src.tools import MediaPipeEstimator

            self._pose_estimator = MediaPipeEstimator()
        return self._pose_estimator

    @property
    def web_search(self):
        if self._web_search is None:
            # 라이브 웹 검색은 MCP 도구(src/mcp_server.py 의 golf_web_search)로 호출합니다.
            from src.web_search import McpWebSearch

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
        prompt = self._prompt(state)
        history = state.get("history", [])[-HISTORY_TURNS:]
        messages = [SystemMessage(SYSTEM_PROMPT)]
        for turn in history:
            messages.append(HumanMessage(turn["content"]) if turn["role"] == "user" else AIMessage(turn["content"]))
        messages.append(HumanMessage(prompt))
        message = self.llm_for(state.get("model")).invoke(messages)
        answer = _text_of(message).strip()
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

    def _prompt(self, state: AgentState) -> str:
        lines = [f"질문: {state['question']}"]
        finding = state.get("finding")
        if finding:
            codes = state.get("issue_codes") or ["문제 없음"]
            measured = []
            for key, value in finding["metrics"].items():
                label, (lo, hi) = METRIC_LABELS[key]
                measured.append(f"{label} {value}° (교육용 기준 {_standard(lo, hi)})")
            lines.append(f"판정: {', '.join(codes)} | " + ", ".join(measured))
            if state.get("memory"):   # 새 판정이면 이전 기록과 비교할 수 있게 함께 줍니다
                lines.append("이전 기록: " + describe(state["memory"]))
            if state.get("practice"):   # 새 판정이면 그동안의 연습도 함께 짚을 수 있게
                lines.append("연습 기록: " + state["practice"].replace("\n", " / "))
        elif self._continues_finding(state):
            # 같은 대화의 직전 판정에 이어지는 질문: 판정 내용과 '무엇을 쟀는지'를 다시 알려 줍니다.
            lines.append(_last_finding_line(state))
        lines.append("근거 문서: " + ", ".join(dict.fromkeys(c["doc_id"] for c in state["contexts"])))
        if any(_is_web(c["doc_id"]) for c in state["contexts"]):
            lines.append(f"(근거가 웹 검색 결과입니다. 조회 날짜: {datetime.date.today().isoformat()})")
        for c in state["contexts"]:
            lines.append(f"\n[{c['doc_id']}]\n{c['text']}")
        return "\n".join(lines)

    @staticmethod
    def _continues_finding(state: AgentState) -> bool:
        """직전 판정이 있고, 이번 근거에 그 판정의 근거 문서가 붙었으면(=판정에 이어지는 질문) True."""
        kind = state.get("last_media_kind")
        if not kind or not state.get("last_metrics"):
            return False
        return any(c["doc_id"] == config.MEDIA_SOURCE[kind] for c in state.get("contexts", []))

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

        pose_fixture 는 테스트·채점 러너만 넣는 값입니다. HTTP API(app.py)는 이 필드를 받지 않습니다.
        model 은 코칭 모델 ID 입니다. 허용 목록 검사는 app.py 가 합니다.
        user_id 를 주면 장기 기억(대화방을 넘어 남는 사용자별 판정 기록)을 읽고 씁니다.
        practice 는 로그인한 사용자의 연습 요약(src/training.practice_summary, 없으면 '')입니다. 게스트는 None.
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
        from src.fakes import FakeCoachLLM
        from src.web_search import FakeWebSearch

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
            from src.memory import build_store

            memory = build_store("live")
        return GolfCoachAgent(llm=llm, checkpointer=checkpointer, web_search=web_search, memory=memory)
    raise ValueError(f"알 수 없는 모드입니다: {mode} (offline 또는 live)")
