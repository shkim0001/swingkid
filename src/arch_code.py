"""아키텍처 다이어그램의 모듈별 '중요 코드' — 화면에서 모듈을 누르면 보여 줍니다.

코드는 요청할 때마다 실제 소스 파일에서 잘라 옵니다. 줄 번호를 적어 두지 않고 함수·클래스 이름(qualname)으로
찾기 때문에(파이썬은 ast, 화면 스크립트는 시작·끝 표시 문자열), 코드가 바뀌어도 다이어그램이 자동으로 따라갑니다.
목록에 있는 모듈만 열 수 있어 임의의 파일을 읽는 입구가 되지 않습니다.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from functools import lru_cache

from src import config


@dataclass(frozen=True)
class Ref:
    file: str                       # 프로젝트 기준 경로
    symbol: str                     # 파이썬: 'Class.method' 또는 'function'. 그 밖: 표시용 이름
    start: str | None = None        # 파이썬이 아닌 파일: 시작 줄에 들어 있는 문자열
    end: str | None = None          # 파이썬이 아닌 파일: 이 문자열이 있는 줄 바로 앞까지
    highlight: tuple[str, ...] = ()  # 강조할 줄(정규식)


@dataclass(frozen=True)
class Module:
    title: str
    summary: str
    refs: tuple[Ref, ...] = field(default_factory=tuple)
    llm: bool = False               # LLM·모델 호출이 일어나는 모듈


LLM_LINE = (r"\.invoke\(", r"ChatBedrockConverse", r"BedrockEmbeddings")

MODULES: dict[str, Module] = {
    "ui": Module(
        "SwingKid 웹 화면",
        "질문과 첨부(사진·영상·GIF), 대화방(thread_id)을 모아 POST /query 로 보내고(로그인 세션은 쿠키로 함께 감), 응답을 판정 막대·각도 그래프·근거로 그립니다.",
        (Ref("src/static/index.html", "ask() — 질문 보내기", "async function ask(", "  function setBusy(",
             (r"fetch\(", r"thread_id", r"video_base64", r"image_base64")),),
    ),
    "api": Module(
        "FastAPI 서버",
        "POST /query 진입점. 입력을 검사(첨부 크기·형식, 모델 허용 목록)한 뒤 에이전트 그래프를 한 번 실행합니다. 에이전트는 잠금으로 하나만 만듭니다.",
        (Ref("src/app.py", "query", highlight=(r"get_agent\(\)\.ask",)), Ref("src/app.py", "get_agent", highlight=(r"_AGENT_LOCK",))),
    ),
    "guard": Module(
        "가드레일",
        "규칙(정규식) 기반이라 같은 질문엔 항상 같은 결과. 의학·구매/가격·인젝션·개인정보는 차단, 골프와 무관하면 golf=False 로 검색 없이 끝냅니다.",
        (Ref("src/guard.py", "check", highlight=(r"return GuardResult",)),),
    ),
    "exit": Module(
        "즉시 응답 (차단 · 골프 무관)",
        "가드레일 단계에서 끝나는 경로. 검색·웹·LLM 을 하나도 부르지 않고 고정 문구로 응답합니다.",
        (Ref("src/agent.py", "GolfCoachAgent._guard", highlight=(r"STATUS_BLOCKED", r"STATUS_NO_ANSWER")),),
    ),
    "supervisor": Module(
        "Supervisor",
        "규칙 기반 라우팅. 사진·영상 → swing_agent, 최신 정보 → info_agent, 그 밖 → knowledge_agent. 문서 근거가 없으면 info_agent 로 한 번 더, 근거가 있으면 coach 로 넘깁니다.",
        (Ref("src/agent.py", "GolfCoachAgent._supervisor", highlight=(r"return route\(",)),
         Ref("src/agent.py", "GolfCoachAgent._build", highlight=(r"add_conditional_edges", r"add_edge\(name")),),
    ),
    "memory": Module(
        "기억 저장소",
        "단기: thread_id 별 체크포인터(대화 이력·직전 판정). 장기: 로그인 계정별 Store 에 판정 코드·각도만 저장하고, '지난번 판정' 질문은 이 기록으로 바로 답합니다.",
        (Ref("src/memory.py", "LongTermMemory.add_finding", highlight=(r"store\.put",)),
         Ref("src/agent.py", "GolfCoachAgent._recall", highlight=(r"describe\(records\)",))),
    ),
    "swing": Module(
        "swing_agent",
        "사진·영상·GIF 에서 관절 좌표를 뽑고, 각도로 문제 코드를 판정합니다. 판정은 LLM 이 아니라 순수 함수가 결정적으로 합니다.",
        (Ref("src/subagents.py", "SwingAgent.run", highlight=(r"analyze_pose\(",)),),
    ),
    "pose": Module(
        "MediaPipe 포즈 + 각도 판정",
        "영상 프레임마다 관절을 찾고(MediaPipe), 백스윙 정점 프레임의 왼팔 각도로 판정합니다. 프레임별 각도는 화면 그래프용으로 함께 돌려줍니다.",
        (Ref("src/tools.py", "analyze_video", highlight=(r"LEAD_ARM_MIN", r"detect_apex")),
         Ref("src/tools.py", "MediaPipeEstimator.estimate_video", highlight=(r"detect_for_video",))),
    ),
    "knowledge": Module(
        "knowledge_agent",
        "내부 골프 지식 문서를 검색합니다. 같은 대화에 직전 판정이 있으면 그 판정의 근거 문서를 앞에 붙여 이어지는 질문에 답하게 합니다.",
        (Ref("src/subagents.py", "KnowledgeAgent.run", highlight=(r"\.search\(question\)",)),),
    ),
    "docs": Module(
        "골프 지식 문서 25개 (검색)",
        "라이브는 단어 검색(BM25)과 의미 검색(Chroma + Titan 임베딩)을 합칩니다. 최신 결과·순위·시세 질문은 문서 범위 밖이라 검색하지 않습니다.",
        (Ref("src/retriever.py", "HybridRetriever.search"),
         Ref("src/retriever.py", "ChromaRetriever.__init__", highlight=LLM_LINE),
         Ref("src/retriever.py", "BM25Retriever.search", highlight=(r"min_score", r"min_coverage"))),
        llm=True,
    ),
    "info": Module(
        "info_agent",
        "웹을 직접 부르지 않고 MCP 도구(golf_web_search)를 호출해 골프 웹 정보를 찾습니다. 결과 URL 이 출처(sources)로 남습니다.",
        (Ref("src/subagents.py", "InfoAgent.run", highlight=(r"searcher\.search",)),
         Ref("src/web_search.py", "McpWebSearch._call", highlight=(r"ainvoke",))),
    ),
    "mcp": Module(
        "MCP 서버 golf-web-search",
        "stdio MCP 서버. info_agent 의 MCP 클라이언트가 자식 프로세스로 띄워 golf_web_search 도구를 부르고, 서버 안에서 DuckDuckGo 로 검색합니다.",
        (Ref("src/mcp_server.py", "golf_web_search", highlight=(r"searcher\.search",)),
         Ref("src/web_search.py", "McpWebSearch.search")),
    ),
    "ddg": Module(
        "DuckDuckGo 웹 검색",
        "API 키 없이 쓰는 검색(ddgs). 질문에 '골프'를 붙여 찾고, 시간 초과면 한 번 더 시도한 뒤 실패하면 빈 결과(→ no_answer)로 끝냅니다.",
        (Ref("src/web_search.py", "DuckDuckGoSearch.search", highlight=(r"DDGS\(",)),),
    ),
    "coach": Module(
        "coach (답변 작성)",
        "모인 근거·판정 각도·이전 기록으로 프롬프트를 만들어 LLM 을 한 번 호출합니다. 판정이었다면 결과를 장기 기억에 저장합니다.",
        (Ref("src/agent.py", "GolfCoachAgent._coach", highlight=LLM_LINE + (r"add_finding",)),
         Ref("src/agent.py", "GolfCoachAgent._prompt")),
        llm=True,
    ),
    "claude": Module(
        "Amazon Bedrock · Claude (LLM)",
        "답변 문장을 만드는 유일한 생성형 LLM 호출. 화면에서 고른 모델 ID 별로 한 번만 만들어 재사용하고, 채점에서는 가짜 모델로 바뀝니다.",
        (Ref("src/agent.py", "default_llm", highlight=LLM_LINE), Ref("src/agent.py", "GolfCoachAgent.llm_for")),
        llm=True,
    ),
}


def _python_range(text: str, symbol: str) -> tuple[int, int]:
    tree = ast.parse(text)
    parts = symbol.split(".")
    body = tree.body
    node = None
    for i, name in enumerate(parts):
        node = next((n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == name), None)
        if node is None:
            raise KeyError(symbol)
        body = getattr(node, "body", [])
    start = min([node.lineno, *(d.lineno for d in getattr(node, "decorator_list", []))])
    return start, node.end_lineno


def _marker_range(lines: list[str], start: str, end: str | None) -> tuple[int, int]:
    first = next(i for i, ln in enumerate(lines) if start in ln)
    last = len(lines) - 1
    if end:
        last = next(i for i in range(first + 1, len(lines)) if end in lines[i]) - 1
    while last > first and not lines[last].strip():
        last -= 1
    return first + 1, last + 1


@lru_cache(maxsize=64)
def _snippet(ref: Ref, mtime: float) -> dict:
    path = config.PROJECT_ROOT / ref.file
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if ref.file.endswith(".py"):
        start, end = _python_range(text, ref.symbol)
    else:
        start, end = _marker_range(lines, ref.start or ref.symbol, ref.end)
    body = lines[start - 1:end]
    patterns = [re.compile(p) for p in ref.highlight]
    marked = [start + i for i, ln in enumerate(body) if any(p.search(ln) for p in patterns)]
    return {"file": ref.file, "symbol": ref.symbol, "start": start, "end": end, "code": "\n".join(body), "highlight": marked}


def code_for(module_id: str) -> dict:
    module = MODULES[module_id]
    snippets = []
    for ref in module.refs:
        path = config.PROJECT_ROOT / ref.file
        snippets.append(_snippet(ref, path.stat().st_mtime))
    return {"module": module_id, "title": module.title, "summary": module.summary, "llm": module.llm, "snippets": snippets}
