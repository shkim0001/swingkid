"""멀티 agent: supervisor 가 질문마다 맞는 sub-agent 를 고르고, 근거가 없을 때만 웹으로 넘기는지 확인한다.

모두 오프라인(가짜 모델 + BM25 + 가짜 웹 검색기 + 관절 좌표 fixture)이라 네트워크·API 호출이 없다.
"""

import sys
import types

from src import config
from src.agent import build_agent
from src.web_search import DuckDuckGoSearch, FakeWebSearch, golf_query
from tests.conftest import load_fixture


def steps(r):
    return [t["step"] for t in r["trace"]]


def make():
    web = FakeWebSearch()
    return build_agent("offline", web_search=web), web


def test_media_goes_to_swing_agent_only():
    agent, web = make()
    r = agent.ask("제 어드레스 자세 봐주세요", pose_fixture=load_fixture("photo_spine_bad"))
    assert r["agents"] == [config.SWING_AGENT]
    assert steps(r) == ["guard", "supervisor", "pose", "coach"]
    assert web.calls == []


def test_pose_failure_ends_without_web_search():
    """자세 인식 실패는 웹으로 넘기지 않고 재촬영 안내로 끝냅니다."""
    agent, web = make()
    r = agent.ask("이 사진 자세 분석해 주세요", pose_fixture=load_fixture("photo_no_person"))
    assert r["status"] == config.STATUS_NO_ANSWER and r["issue_codes"] == [config.POSE_NOT_DETECTED]
    assert r["agents"] == [config.SWING_AGENT] and web.calls == [] and agent.llm.calls == 0


def test_knowledge_question_does_not_touch_the_web():
    agent, web = make()
    r = agent.ask("벙커에 빠졌을 때는 어떻게 쳐야 하나요?")
    assert r["agents"] == [config.KNOWLEDGE_AGENT] and "short_game.md" in r["sources"]
    assert web.calls == []


def test_knowledge_miss_falls_back_to_info_agent():
    """내부 문서에 없는 골프 질문은 supervisor 가 웹 검색으로 한 번 더 찾습니다."""
    agent, web = make()
    r = agent.ask("타이거 우즈의 2000년 US오픈 최종 성적이 몇 타였나요?")
    assert r["agents"] == [config.KNOWLEDGE_AGENT, config.INFO_AGENT]
    assert steps(r) == ["guard", "supervisor", "retrieve", "supervisor", "web_search", "coach"]
    assert r["status"] == config.STATUS_ANSWERED
    assert all(s.startswith("https://") for s in r["sources"])
    # 코칭 모델은 웹 근거라는 것과 조회 날짜를 함께 받습니다
    assert "웹 검색 결과" in agent.llm.last_messages[-1].content


def test_fresh_info_question_goes_straight_to_the_web():
    agent, web = make()
    r = agent.ask("올해 LPGA 투어 상금 순위 1위가 누구예요?")
    assert r["agents"] == [config.INFO_AGENT] and "retrieve" not in steps(r)
    assert r["status"] == config.STATUS_ANSWERED and r["sources"][0].startswith("https://")


def test_nothing_on_the_web_either_is_no_answer_without_llm():
    agent, web = make()
    r = agent.ask("여름 라운드 때 선크림은 뭘 발라요?")
    assert r["agents"] == [config.KNOWLEDGE_AGENT, config.INFO_AGENT]
    assert r["status"] == config.STATUS_NO_ANSWER and r["sources"] == [] and r["answer"] == config.NO_ANSWER_TEXT
    assert agent.llm.calls == 0


def test_blocked_and_off_topic_questions_never_reach_supervisor():
    agent, web = make()
    for q in ("드라이버 브랜드 추천해 주세요", "오늘 서울 날씨 어때요?"):
        r = agent.ask(q)
        assert r["agents"] == [] and steps(r) == ["guard"]
    assert web.calls == []


def test_golf_query_adds_golf_word():
    assert golf_query("타이거 우즈 US오픈") == "골프 타이거 우즈 US오픈"
    assert golf_query("LPGA 골프 상금") == "LPGA 골프 상금"


def test_live_web_search_failure_becomes_empty_result(monkeypatch):
    """라이브 웹 검색이 실패해도 예외 없이 빈 결과(→ no_answer)가 됩니다."""

    class BrokenDDGS:
        def __init__(self, **kwargs):
            pass

        def text(self, *args, **kwargs):
            raise RuntimeError("rate limited")

    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=BrokenDDGS))
    search = DuckDuckGoSearch()
    assert search.search("LPGA 상금 순위") == [] and "rate limited" in search.last_error


def test_live_web_search_keeps_only_results_with_url_and_text(monkeypatch):
    seen = {}

    class FakeDDGS:
        def __init__(self, **kwargs):
            pass

        def text(self, query, **kwargs):
            seen["query"] = query
            return [
                {"title": "A", "href": "https://a.example/x", "body": "본문"},
                {"title": "B", "href": "", "body": "주소 없음"},
                {"title": "C", "href": "https://c.example/y", "body": ""},
            ]

    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=FakeDDGS))
    results = DuckDuckGoSearch().search("LPGA 상금 순위")
    assert [r.url for r in results] == ["https://a.example/x"]
    assert seen["query"] == "골프 lpga 상금 순위"   # 키워드 검색어(형태소 분석 결과는 소문자)


def test_live_web_search_falls_back_to_next_backend_and_keywords(monkeypatch):
    """한 검색 엔진이 막히거나(시간 초과) 문장 검색에 결과가 없으면, 다음 엔진·키워드 검색어로 넘어갑니다."""
    calls = []

    class FlakyDDGS:
        def __init__(self, **kwargs):
            pass

        def text(self, query, backend="auto", **kwargs):
            calls.append((backend, query))
            if backend == "duckduckgo":
                raise RuntimeError("TimeoutException: html.duckduckgo.com")
            if backend == "yahoo" and "몇" in query:       # 문장 검색어에만 결과가 있는 경우
                return [{"title": "2000 U.S. Open", "href": "https://en.wikipedia.org/wiki/2000_U.S._Open_(golf)", "body": "Woods 272 (-12)"}]
            raise RuntimeError("No results found.")

    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=FlakyDDGS))
    search = DuckDuckGoSearch(backends=("duckduckgo", "yahoo"))
    results = search.search("타이거 우즈의 2000년 US오픈 최종 성적이 몇 타였나요?")
    assert [r.url for r in results] == ["https://en.wikipedia.org/wiki/2000_U.S._Open_(golf)"]
    assert search.last_error is None
    assert calls[0][0] == "duckduckgo" and "몇" not in calls[0][1]                        # 키워드 먼저
    assert calls[-1] == ("yahoo", "골프 타이거 우즈의 2000년 US오픈 최종 성적이 몇 타였나요?")  # 다음에 문장


def test_keyword_query_keeps_nouns_numbers_and_latin():
    from src.web_search import keyword_query

    q = keyword_query("타이거 우즈의 2000년 US오픈 최종 성적이 몇 타였나요?")
    assert "2000" in q and "타이거" in q and "us" in q.lower() and "몇" not in q


def test_live_web_search_stops_at_total_timeout(monkeypatch):
    """모든 엔진이 실패해도 전체 시간 제한 안에서 끝내고 빈 목록 + 오류를 돌려줍니다."""
    calls = []

    class FailingDDGS:
        def __init__(self, **kwargs):
            pass

        def text(self, query, backend="auto", **kwargs):
            calls.append(backend)
            raise RuntimeError("No results found.")

    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=FailingDDGS))
    search = DuckDuckGoSearch(total_timeout=0)
    assert search.search("타이거 우즈 2000 US오픈 성적") == [] and "시간 제한" in search.last_error
    assert len(calls) <= 1
