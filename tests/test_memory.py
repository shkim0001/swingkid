"""장기 기억: 사용자(user_id)별 자세 판정 기록이 대화방(thread_id)을 넘어 이어지는지 확인한다."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from src import app as app_module
from src import config
from src.agent import build_agent
from src.memory import MEMORY_DOC_ID, LongTermMemory, describe, is_recall_question
from tests.conftest import load_fixture

RECALL_Q = "지난번 내 자세 판정 결과랑 비교해서 알려 주세요"


def steps(r):
    return [t["step"] for t in r["trace"]]


def test_finding_is_saved_and_recalled_in_a_new_thread(agent):
    first = agent.ask("어드레스 봐주세요", thread_id="t-a", user_id="user-1", pose_fixture=load_fixture("photo_spine_bad"))
    assert steps(first)[-1] == "memory"   # 판정 뒤 장기 기억에 저장
    records = agent.memory.findings("user-1")
    assert len(records) == 1 and records[0]["kind"] == "photo" and records[0]["issue_codes"] == [config.SPINE_TILT]

    # 다른 대화방에서 물어도 기억합니다. sub-agent·웹 검색 없이 장기 기억을 근거로 답합니다.
    calls = agent.llm.calls
    r = agent.ask(RECALL_Q, thread_id="t-b", user_id="user-1")
    assert r["status"] == config.STATUS_ANSWERED and r["agents"] == []
    assert steps(r) == ["guard", "memory", "coach"]
    assert r["sources"][:2] == [MEMORY_DOC_ID, "address.md"]
    assert agent.llm.calls == calls + 1
    assert "척추 기울기 기준 벗어남" in r["contexts"][0]["text"]


def test_new_finding_is_compared_with_previous_ones(agent):
    agent.ask("어드레스 봐주세요", thread_id="t-1", user_id="user-2", pose_fixture=load_fixture("photo_spine_bad"))
    agent.ask("다시 찍었어요", thread_id="t-2", user_id="user-2", pose_fixture=load_fixture("photo_normal"))
    prompt = agent.llm.last_messages[-1].content
    assert "이전 기록: 이 사용자의 이전 자세 판정 1건" in prompt   # 두 번째 판정 때 첫 판정을 비교 근거로 받음
    assert len(agent.memory.findings("user-2")) == 2


def test_memory_is_per_user_and_needs_user_id(agent):
    agent.ask("어드레스 봐주세요", user_id="user-3", pose_fixture=load_fixture("photo_spine_bad"))
    agent.ask("어드레스 봐주세요", pose_fixture=load_fixture("photo_spine_bad"))   # user_id 없음 → 저장 안 함
    other = agent.ask(RECALL_Q, user_id="user-4")
    assert other["status"] == config.STATUS_NO_ANSWER and other["sources"] == []
    assert config.MEMORY_EMPTY_TEXT in other["answer"] and agent.llm.calls == 2
    assert len(agent.memory.findings("user-3")) == 1


def test_failed_pose_is_not_remembered(agent):
    agent.ask("이 사진 봐주세요", user_id="user-5", pose_fixture=load_fixture("photo_no_person"))
    assert agent.memory.findings("user-5") == []


def test_in_thread_follow_up_still_uses_short_term_memory(agent):
    """같은 대화의 '아까 …'는 단기 기억(대화방)이 맡고, 장기 기억 경로로 가지 않습니다."""
    agent.ask("어드레스 봐주세요", thread_id="t-s", user_id="user-6", pose_fixture=load_fixture("photo_spine_bad"))
    r = agent.ask("아까 알려준 교정 방법은 연습장에서 어떻게 연습하면 돼요?", thread_id="t-s", user_id="user-6")
    assert "memory" not in steps(r)[:2] and "address.md" in r["sources"]


@pytest.mark.parametrize("q, expected", [
    ("지난번 판정이랑 비교해 줘", True),
    ("내 기록 보여 줘", True),
    ("예전에 올린 사진보다 좋아졌나요?", True),
    ("아까 알려준 교정 방법은요?", False),
    ("드라이버 그립은 어떻게 잡아요?", False),
])
def test_recall_question_detection(q, expected):
    assert is_recall_question(q) is expected


def test_sqlite_store_survives_restart(tmp_path):
    """라이브 저장소(SQLite)는 프로세스를 다시 띄워도 기록이 남습니다."""
    from langgraph.store.sqlite import SqliteStore

    path = tmp_path / "memory.sqlite"

    def open_memory():
        store = SqliteStore(sqlite3.connect(path, check_same_thread=False, isolation_level=None))
        store.setup()
        return LongTermMemory(store)

    open_memory().add_finding("user-7", "video", [config.BENT_LEAD_ARM], {"lead_arm": 141.2})
    again = open_memory()
    assert again.findings("user-7")[0]["metrics"] == {"lead_arm": 141.2}
    assert again.clear("user-7") == 1 and again.findings("user-7") == []


def test_describe_marks_repeated_problems():
    records = [
        {"at": "2026-11-11T10:00:00", "kind": "photo", "issue_codes": [config.SPINE_TILT], "metrics": {"spine_tilt": 20.1}},
        {"at": "2026-11-10T10:00:00", "kind": "photo", "issue_codes": [config.SPINE_TILT], "metrics": {"spine_tilt": 18.0}},
    ]
    text = describe(records)
    assert "2건" in text and "반복된 문제: 척추 기울기 기준 벗어남" in text


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GOLF_MODE", "offline")
    app_module.get_agent.cache_clear()
    yield TestClient(app_module.app)
    app_module.get_agent.cache_clear()


def test_memory_api_round_trip(client):
    body = {"question": "어드레스 봐주세요", "fixture": "photo_spine_bad", "user_id": "web-abc123"}
    assert client.post("/demo/fixture", json=body).status_code == 200
    listed = client.get("/memory/web-abc123").json()
    assert listed["count"] == 1 and listed["findings"][0]["issue_codes"] == [config.SPINE_TILT]
    r = client.post("/query", json={"question": RECALL_Q, "user_id": "web-abc123"}).json()
    assert r["status"] == "answered" and MEMORY_DOC_ID in r["sources"]
    assert client.delete("/memory/web-abc123").json()["deleted"] == 1
    assert client.get("/memory/web-abc123").json()["count"] == 0


def test_bad_user_id_is_rejected(client):
    assert client.post("/query", json={"question": "그립", "user_id": "a b"}).status_code == 422
    assert client.get("/memory/a").status_code == 422


def test_web_search_fixture_is_not_offered_as_pose_sample(client):
    assert "web_search" not in client.get("/demo/samples").json()["fixtures"]
    assert client.post("/demo/fixture", json={"question": "봐주세요", "fixture": "web_search"}).status_code == 404


def test_korean_nickname_and_moving_records(client):
    body = {"question": "어드레스 봐주세요", "fixture": "photo_spine_bad", "user_id": "user-auto123"}
    assert client.post("/demo/fixture", json=body).status_code == 200
    moved = client.post("/memory/user-auto123/move", json={"to": "골프키드"}).json()
    assert moved["moved"] == 1
    assert client.get("/memory/골프키드").json()["count"] == 1
    assert client.get("/memory/user-auto123").json()["count"] == 0
    r = client.post("/query", json={"question": RECALL_Q, "user_id": "골프키드"}).json()
    assert r["status"] == "answered" and MEMORY_DOC_ID in r["sources"]
    assert client.post("/memory/골프키드/move", json={"to": "띄어 쓰기"}).status_code == 422
