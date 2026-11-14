"""웹 검색 도구 — 골프 정보 agent(info_agent)가 씁니다.

세 검색기 모두 `search(query) -> list[WebResult]` 하나만 가집니다(주입 가능 구조).

0. McpWebSearch — 라이브 기본값. info_agent 는 웹을 직접 부르지 않고 MCP 도구(golf_web_search)를 호출합니다.
   도구는 src/web_info/mcp_server.py(stdio MCP 서버)가 노출하고, 서버 안에서 아래 DuckDuckGoSearch 로 검색합니다.
1. DuckDuckGoSearch — MCP 서버 안에서 쓰는 실제 검색기. API 키 없이 쓰는 ddgs 패키지로 검색합니다.
   엔진 여러 개(config.WEB_BACKENDS)와 검색어 2개(키워드 → 문장)를 차례로 시도합니다.
   질문에 '골프'가 없으면 앞에 붙여 골프 관련 결과만 찾게 합니다.
   패키지가 없거나 네트워크 오류가 나면 예외를 올리지 않고 빈 목록을 돌려줘 no_answer 로 끝나게 합니다.
2. FakeWebSearch — 자동 채점·테스트용. data/fixtures/web_search.json 의 고정 결과를 돌려줍니다.
   실제 네트워크 호출이 없고, 호출된 질문을 calls 에 남겨 '웹 검색을 부르면 안 되는 경로'도 확인할 수 있습니다.

결과 한 건은 contexts 의 {doc_id, text} 로 들어갑니다. doc_id 는 출처 URL 이라 sources 에 URL 이 남습니다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
import time
from pathlib import Path

from src.shared import config
from src.web_info.schemas import WebResult

log = logging.getLogger(__name__)

WEB_FIXTURE = config.FIXTURES_DIR / "web_search.json"


def golf_query(query: str) -> str:
    """검색어에 '골프'가 없으면 붙입니다. ('타이거 우즈 US오픈' → '골프 타이거 우즈 US오픈')"""
    return query if "골프" in query or "golf" in query.lower() else f"골프 {query}"


def keyword_query(query: str) -> str:
    """질문 문장에서 명사·숫자·영문만 남긴 검색어 ('…성적이 몇 타였나요?' → '타이거 우즈 2000 us 오픈 최종 성적').

    검색 엔진에 따라 질문 문장을 통째로 넣으면 결과가 없고 키워드로 넣어야 나오는 경우가 있어 두 번째 검색어로 씁니다.
    형태소 분석은 지식 검색(BM25)과 같은 kiwipiepy 토큰화를 그대로 씁니다.
    """
    from src.knowledge.retriever import tokenize

    return " ".join(tokenize(query))


class DuckDuckGoSearch:
    """라이브용 웹 검색기 (ddgs, API 키 불필요).

    한 엔진이 막히거나 결과가 없어도 멈추지 않도록, 검색어 2개(키워드 → 질문 문장) × 엔진 여러 개(config.WEB_BACKENDS)를
    차례로 시도하고 처음 나온 결과를 씁니다. 모두 실패하면 빈 목록 + last_error 로 no_answer 가 되게 합니다.
    """

    def __init__(self, max_results: int = config.WEB_TOP_K, region: str = "kr-kr", timeout: int = config.WEB_ATTEMPT_TIMEOUT,
                 backends: tuple[str, ...] = config.WEB_BACKENDS, total_timeout: float = config.WEB_TOTAL_TIMEOUT):
        self.max_results = max_results
        self.region = region
        self.timeout = timeout
        self.backends = backends
        self.total_timeout = total_timeout
        self.last_error: str | None = None

    def queries(self, query: str) -> list[str]:
        """키워드 검색어를 먼저, 질문 문장을 다음에 씁니다(측정상 키워드 쪽 성공률이 높음)."""
        sentence = golf_query(query)
        keywords = keyword_query(query)
        return [sentence] if not keywords or golf_query(keywords) == sentence else [golf_query(keywords), sentence]

    def search(self, query: str) -> list[WebResult]:
        self.last_error = None
        errors = []
        try:
            from ddgs import DDGS
        except Exception as exc:  # 패키지 없음
            self.last_error = f"{type(exc).__name__}: {exc}"
            return []
        deadline = time.monotonic() + self.total_timeout
        for q in self.queries(query):
            for backend in self.backends:
                if time.monotonic() >= deadline:
                    errors.append("전체 시간 제한 초과")
                    break
                try:
                    rows = DDGS(timeout=self.timeout).text(q, region=self.region, max_results=self.max_results, backend=backend)
                except Exception as exc:  # 네트워크·검색 제한·결과 없음 → 다음 엔진
                    errors.append(f"{backend}: {type(exc).__name__}: {exc}"[:200])
                    continue
                results = self._parse(rows)
                if results:
                    self.last_error = None
                    return results
                errors.append(f"{backend}: 결과 없음")
        self.last_error = " | ".join(errors) or "결과 없음"
        log.warning("웹 검색 실패: %s", self.last_error)
        return []

    def _parse(self, rows) -> list[WebResult]:
        results = []
        for row in rows or []:
            url, body = row.get("href") or "", (row.get("body") or "").strip()
            if url.startswith(("http://", "https://")) and body:
                results.append(WebResult(url=url, title=(row.get("title") or "").strip(), text=body))
        return results[: self.max_results]


class McpWebSearch:
    """라이브용 웹 검색기 — MCP 도구 호출.

    src/web_info/mcp_server.py 를 stdio MCP 서버로 띄우고(langchain-mcp-adapters MultiServerMCPClient),
    `golf_web_search` 도구를 불러 결과를 받습니다. 실제 검색(ddgs)은 서버 쪽 DuckDuckGoSearch 가 합니다.
    도구 목록은 처음 한 번만 받아 두고, 호출마다 서버 프로세스를 새로 띄웁니다(세션 없는 호출).
    MCP 패키지가 없거나 서버·도구 호출이 실패하면 빈 목록 + last_error 로 no_answer 가 되게 합니다.
    """

    def __init__(self, max_results: int = config.WEB_TOP_K, timeout: float = 60.0):
        self.max_results = max_results
        self.timeout = timeout
        self.last_error: str | None = None
        self._tool = None
        self._loop = None
        self._lock = threading.Lock()

    # ── 동기 코드(그래프 노드)에서 비동기 MCP 클라이언트를 쓰기 위한 전용 이벤트 루프 ──
    def _run(self, coro):
        with self._lock:
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
                threading.Thread(target=self._loop.run_forever, name="mcp-web-search", daemon=True).start()
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=self.timeout)

    def connection(self) -> dict:
        from src.web_info.mcp_server import SERVER_NAME

        return {
            SERVER_NAME: {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", "src.web_info.mcp_server"],
                "cwd": str(config.PROJECT_ROOT),
            }
        }

    async def _load_tool(self):
        from langchain_mcp_adapters.client import MultiServerMCPClient

        from src.web_info.mcp_server import TOOL_NAME

        tools = await MultiServerMCPClient(self.connection()).get_tools()
        for tool in tools:
            if tool.name == TOOL_NAME:
                return tool
        raise RuntimeError(f"MCP 서버에 {TOOL_NAME} 도구가 없습니다")

    async def _call(self, query: str):
        if self._tool is None:
            self._tool = await self._load_tool()
        return await self._tool.ainvoke({"query": query, "max_results": self.max_results})

    def search(self, query: str) -> list[WebResult]:
        self.last_error = None
        try:
            payload = parse_tool_output(self._run(self._call(query)))
        except Exception as exc:  # MCP 패키지 없음·서버 실행 실패·시간 초과 등
            self.last_error = f"MCP {type(exc).__name__}: {exc}"
            log.warning("MCP 웹 검색 실패: %s", self.last_error)
            return []
        self.last_error = payload.get("error")
        results = []
        for row in payload.get("results") or []:
            url, body = row.get("url") or "", (row.get("text") or "").strip()
            if url.startswith(("http://", "https://")) and body:
                results.append(WebResult(url=url, title=(row.get("title") or "").strip(), text=body))
        return results[: self.max_results]


def parse_tool_output(output) -> dict:
    """MCP 도구 결과(문자열 / 콘텐츠 블록 목록 / dict)를 {"results", "error"} dict 로 바꿉니다."""
    if isinstance(output, tuple):  # (content, artifact) 형태
        output = output[0]
    if isinstance(output, dict) and "results" in output:
        return output
    if isinstance(output, list):
        output = "".join(
            b.get("text", "") if isinstance(b, dict) else getattr(b, "text", str(b)) for b in output
        )
    data = json.loads(output)
    if not isinstance(data, dict):
        raise ValueError("MCP 도구 결과 형식이 다릅니다")
    return data


class FakeWebSearch:
    """채점용 가짜 웹 검색기. 질문에 keywords 가 모두 들어 있는 첫 항목의 결과를 돌려줍니다."""

    def __init__(self, path: Path = WEB_FIXTURE):
        self.entries = json.loads(path.read_text(encoding="utf-8"))["entries"]
        self.calls: list[str] = []
        self.last_error: str | None = None

    def search(self, query: str) -> list[WebResult]:
        self.calls.append(query)
        for entry in self.entries:
            if all(k.lower() in query.lower() for k in entry["keywords"]):
                return [WebResult(**r) for r in entry["results"]]
        return []
