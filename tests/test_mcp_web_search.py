"""info_agent 의 라이브 웹 검색이 MCP 도구(golf_web_search)를 거치는지 확인합니다.

실제 네트워크 검색은 하지 않습니다. stdio MCP 서버를 띄워 도구 목록만 받아 보는 테스트는
mcp·langchain-mcp-adapters 가 없으면 건너뜁니다.
"""

import asyncio
import json

import pytest

from src.shared import config
from src.coach.graph import GolfCoachAgent
from src.web_info.mcp_server import TOOL_NAME, golf_web_search
from src.web_info.search import McpWebSearch, parse_tool_output


class FakeTool:
    name = TOOL_NAME

    def __init__(self, output):
        self.output = output
        self.calls = []

    async def ainvoke(self, args):
        self.calls.append(args)
        return self.output


def test_live_agent_uses_mcp_web_search_by_default():
    assert isinstance(GolfCoachAgent(llm=object()).web_search, McpWebSearch)


def test_parse_tool_output_accepts_text_blocks_and_dict():
    payload = {"results": [{"url": "https://a.example", "title": "A", "text": "본문"}], "error": None}
    text = json.dumps(payload, ensure_ascii=False)
    assert parse_tool_output(text) == payload
    assert parse_tool_output([{"type": "text", "text": text}]) == payload
    assert parse_tool_output(payload) == payload
    assert parse_tool_output((text, None)) == payload


def test_mcp_search_calls_tool_and_keeps_valid_results():
    search = McpWebSearch(max_results=2)
    search._tool = FakeTool(json.dumps({
        "results": [
            {"url": "https://a.example/x", "title": "A", "text": "본문"},
            {"url": "", "title": "B", "text": "주소 없음"},
            {"url": "https://c.example/y", "title": "C", "text": ""},
        ],
        "error": None,
    }))
    results = search.search("LPGA 상금 순위")
    assert [r.url for r in results] == ["https://a.example/x"]
    assert search._tool.calls == [{"query": "LPGA 상금 순위", "max_results": 2}]
    assert search.last_error is None


def test_mcp_search_passes_server_error_through():
    search = McpWebSearch()
    search._tool = FakeTool(json.dumps({"results": [], "error": "RuntimeError: rate limited"}))
    assert search.search("LPGA") == [] and "rate limited" in search.last_error


def test_mcp_server_failure_becomes_empty_result(monkeypatch):
    search = McpWebSearch(timeout=30)
    monkeypatch.setattr(search, "connection", lambda: {"golf-web-search": {
        "transport": "stdio", "command": "no-such-command-xyz", "args": []}})
    assert search.search("LPGA") == [] and search.last_error.startswith("MCP ")


def test_mcp_server_tool_function_returns_structured_result(monkeypatch):
    import sys, types

    class FakeDDGS:
        def __init__(self, **kwargs):
            pass

        def text(self, query, **kwargs):
            return [{"title": "A", "href": "https://a.example/x", "body": "본문"}]

    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=FakeDDGS))
    out = golf_web_search("LPGA 상금 순위", max_results=3)
    assert out == {"results": [{"url": "https://a.example/x", "title": "A", "text": "본문"}], "error": None}


def test_stdio_mcp_server_exposes_golf_web_search_tool():
    pytest.importorskip("langchain_mcp_adapters")
    tool = asyncio.run(McpWebSearch()._load_tool())
    assert tool.name == TOOL_NAME
    schema = tool.args_schema if isinstance(tool.args_schema, dict) else tool.args_schema.model_json_schema()
    assert "query" in schema["properties"]
