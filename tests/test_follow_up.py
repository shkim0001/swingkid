"""스윙 판정 뒤 같은 대화에서 다른 부위(손목 등)를 묻는 이어지는 질문.

계기: 영상 판정 뒤 '손목 스냅은 어때?'에 '자료를 보내 달라'는 답이 나옴.
  - 측정하지 않은 부위라 직전 판정 수치를 다시 알려 주고, 무엇을 쟀는지 밝혀야 함
  - '코킹은 괜찮아요?'는 근거 없음, '손목은 어떻게 써야 해요?'는 퍼팅 문서로 답하던 문제
"""

import json

import pytest

from src import config
from src.agent import build_agent
from src.guard import about_pose

FIXTURE = json.loads((config.FIXTURES_DIR / "video_bent_arm.json").read_text(encoding="utf-8"))


@pytest.fixture
def agent():
    a = build_agent("offline")
    a.ask("제 스윙 영상을 봐 주세요", pose_fixture=FIXTURE, thread_id="t")
    return a


@pytest.mark.parametrize("question", ["손목 스냅은 어때?", "손목 스냅도 봐줘", "코킹은 괜찮아요?", "손목은 어떻게 써야 해요?"])
def test_wrist_follow_up_continues_swing_finding(agent, question):
    r = agent.ask(question, thread_id="t")
    assert r["status"] == config.STATUS_ANSWERED
    assert r["sources"][0] == "backswing.md"   # 직전 영상 판정의 근거 문서가 맨 앞


def test_follow_up_prompt_repeats_last_finding_and_what_was_measured(agent):
    agent.ask("손목 스냅은 어때?", thread_id="t")
    state = agent.graph.get_state({"configurable": {"thread_id": "t"}}).values
    prompt = agent._prompt(state)
    assert "직전 판정(같은 대화, 정면 스윙 영상): bent_lead_arm" in prompt
    assert "측정한 항목은 백스윙 정점 왼팔 각도뿐" in prompt


def test_unrelated_follow_up_does_not_carry_finding(agent):
    r = agent.ask("버디가 뭐예요?", thread_id="t")
    assert r["sources"] == ["scoring.md"]
    state = agent.graph.get_state({"configurable": {"thread_id": "t"}}).values
    assert "직전 판정" not in agent._prompt(state)


def test_wrist_questions_answer_from_docs_without_swing():
    a = build_agent("offline")
    assert "backswing.md" in a.ask("손목 코킹이 뭐예요?")["sources"]
    assert "downswing.md" in a.ask("손목 스냅은 언제 풀어요?")["sources"]


def test_about_pose_rule():
    assert about_pose("손목 스냅도 봐줘") and about_pose("코킹은 괜찮아요?")
    assert not about_pose("버디가 뭐예요?")
