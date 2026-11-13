"""골프 웹 검색 MCP 서버 — info_agent 가 MCP 도구로 호출합니다.

실행: `python -m src.mcp_server` (stdio 전송). 보통은 직접 띄우지 않고
src/web_search.py 의 McpWebSearch 가 MCP 클라이언트(langchain-mcp-adapters)로 자식 프로세스로 띄웁니다.

노출 도구
- golf_web_search(query, max_results): ddgs 로 골프 웹 검색(엔진 여러 개를 차례로 시도: config.WEB_BACKENDS).
  결과는 {"results": [{url, title, text}], "error": str | None} 구조로 돌려줘
  클라이언트가 출처 URL 을 그대로 sources 에 쓸 수 있습니다.

stdio 서버라 표준 출력은 MCP 메시지 전용입니다. 로그는 표준 오류로만 남깁니다.
"""

from __future__ import annotations

import logging
import sys

from mcp.server.fastmcp import FastMCP

from src import config
from src.web_search import DuckDuckGoSearch

logging.basicConfig(stream=sys.stderr, level=logging.WARNING)

SERVER_NAME = "golf-web-search"
TOOL_NAME = "golf_web_search"

mcp = FastMCP(SERVER_NAME)


@mcp.tool(name=TOOL_NAME)
def golf_web_search(query: str, max_results: int = config.WEB_TOP_K) -> dict:
    """골프 관련 웹 검색. 질문에 '골프'가 없으면 붙여 검색하고, 출처 URL·제목·요약을 돌려줍니다."""
    searcher = DuckDuckGoSearch(max_results=max(1, min(max_results, 10)))
    results = searcher.search(query)
    return {
        "results": [{"url": r.url, "title": r.title, "text": r.text} for r in results],
        "error": searcher.last_error,
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
