"""웹 검색 도구 — 골프 정보 agent(info_agent)가 씁니다.

세 검색기 모두 `search(query) -> list[WebResult]` 하나만 가집니다(주입 가능 구조).

0. McpWebSearch — 라이브 기본값. info_agent 는 웹을 직접 부르지 않고 MCP 도구(golf_web_search)를 호출합니다.
   도구는 src/mcp_server.py(stdio MCP 서버)가 노출하고, 서버 안에서 아래 DuckDuckGoSearch 로 검색합니다.
1. DuckDuckGoSearch — MCP 서버 안에서 쓰는 실제 검색기. API 키 없이 쓰는 ddgs 패키지로 검색합니다.
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
from dataclasses import dataclass
from pathlib import Path

from src import config

log = logging.getLogger(__name__)

WEB_FIXTURE = config.FIXTURES_DIR / "web_search.json"


@dataclass(frozen=True)
class WebResult:
    url: str
    title: str
    text: str


def golf_query(query: str) -> str:
    """검색어에 '골프'가 없으면 붙입니다. ('타이거 우즈 US오픈' → '골프 타이거 우즈 US오픈')"""
    return query if "골프" in query or "golf" in query.lower() else f"골프 {query}"


class DuckDuckGoSearch:
    """라이브용 웹 검색기 (ddgs, API 키 불필요)."""

    def __init__(self, max_results: int = config.WEB_TOP_K, region: str = "kr-kr", timeout: int = 10, attempts: int = 2):
        self.max_results = max_results
        self.region = region
        self.timeout = timeout
        self.attempts = attempts  # 검색 서버가 가끔 시간 초과로 실패해 한 번 더 시도합니다
        self.last_error: str | None = None

    def search(self, query: str) -> list[WebResult]:
        self.last_error = None
        rows = None
        for _ in range(self.attempts):
            try:
                from ddgs import DDGS

                rows = DDGS(timeout=self.timeout).text(golf_query(query), region=self.region, max_results=self.max_results)
                self.last_error = None
                break
            except Exception as exc:  # 패키지 없음·네트워크·검색 제한 등은 '근거 없음'으로 처리합니다
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.warning("웹 검색 실패: %s", self.last_error)
        if rows is None:
            return []
        results = []
        for row in rows or []:
            url, body = row.get("href") or "", (row.get("body") or "").strip()
            if url.startswith(("http://", "https://")) and body:
                results.append(WebResult(url=url, title=(row.get("title") or "").strip(), text=body))
        return results[: self.max_results]


class McpWebSearch:
    """라이브용 웹 검색기 — MCP 도구 호출.

    src/mcp_server.py 를 stdio MCP 서버로 띄우고(langchain-mcp-adapters MultiServerMCPClient),
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
        from src.mcp_server import SERVER_NAME

        return {
            SERVER_NAME: {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["-m", "src.mcp_server"],
                "cwd": str(config.PROJECT_ROOT),
            }
        }

    async def _load_tool(self):
        from langchain_mcp_adapters.client import MultiServerMCPClient

        from src.mcp_server import TOOL_NAME

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
