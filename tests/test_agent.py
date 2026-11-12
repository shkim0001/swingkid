"""에이전트 흐름: 응답 형식, 경로별 LLM 호출 여부, 대화 이어가기."""

from src import config
from tests.conftest import load_fixture

STANDARD_KEYS = {"answer", "contexts", "trace"}
EXTRA_KEYS = {"status", "issue_codes", "sources"}


def test_text_answer_has_standard_and_extra_fields(agent):
    r = agent.ask("드라이버 그립은 어떻게 잡아야 하나요?")
    assert STANDARD_KEYS | EXTRA_KEYS <= r.keys()
    assert r["status"] == config.STATUS_ANSWERED
    assert "grip.md" in r["sources"]
    assert all(set(c) == {"doc_id", "text"} for c in r["contexts"])
    assert [t["step"] for t in r["trace"]] == ["guard", "supervisor", "retrieve", "coach"]
    assert r["agents"] == [config.KNOWLEDGE_AGENT]


def test_no_answer_does_not_call_llm(agent):
    r = agent.ask("여름 라운드 때 선크림은 뭘 발라요?")  # 골프 질문이지만 문서에 근거 없음
    assert r["status"] == config.STATUS_NO_ANSWER
    assert r["answer"] == config.NO_ANSWER_TEXT and r["sources"] == []
    assert agent.llm.calls == 0


def test_blocked_skips_everything(agent):
    r = agent.ask("드라이버 브랜드 추천해 주세요")
    assert r["status"] == config.STATUS_BLOCKED and r["issue_codes"] == [config.GUARD_PURCHASE]
    assert [t["step"] for t in r["trace"]] == ["guard"]
    assert agent.llm.calls == 0


def test_pose_failure_short_circuits(agent):
    r = agent.ask("이 사진 봐주세요", pose_fixture=load_fixture("photo_no_person"))
    assert r["status"] == config.STATUS_NO_ANSWER
    assert r["issue_codes"] == [config.POSE_NOT_DETECTED] and r["sources"] == []
    assert r["answer"].startswith(config.NO_ANSWER_TEXT)
    assert agent.llm.calls == 0


def test_photo_finding_uses_fixed_source(agent):
    r = agent.ask("어드레스 봐주세요", pose_fixture=load_fixture("photo_knee_bad"))
    assert r["issue_codes"] == [config.KNEE_FLEX] and r["sources"] == ["address.md"]


def test_follow_up_keeps_thread_context(agent):
    agent.ask("제 어드레스 자세 봐주세요", pose_fixture=load_fixture("photo_spine_bad"), thread_id="t-1")
    r = agent.ask("아까 알려준 교정 방법은 연습장에서 어떻게 연습하면 돼요?", thread_id="t-1")
    assert r["status"] == config.STATUS_ANSWERED and "address.md" in r["sources"]
    # 다른 대화에서는 이어받을 사진 판정이 없으므로 address.md 를 붙이지 않습니다
    # (일반 연습법 문서 practice.md 로 답하는 것은 괜찮음)
    other = agent.ask("아까 알려준 교정 방법은 연습장에서 어떻게 연습하면 돼요?", thread_id="t-2")
    assert "address.md" not in other["sources"]


def test_model_choice_picks_llm_per_request(monkeypatch):
    """모델을 고르면 그 ID로 만든 모델이 답하고, 고르지 않으면 고정 스택 기본 모델이 답합니다(실제 호출 없음)."""
    from src import agent as agent_module
    from src.agent import GolfCoachAgent
    from src.fakes import FakeCoachLLM
    from src.retriever import BM25Retriever

    made = {}

    def fake_default_llm(model_id=config.MODEL_ID):
        made[model_id] = FakeCoachLLM()
        return made[model_id]

    monkeypatch.setattr(agent_module, "default_llm", fake_default_llm)
    coach = GolfCoachAgent(retriever=BM25Retriever())
    haiku = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

    r1 = coach.ask("드라이버 그립은 어떻게 잡아야 하나요?", model=haiku)
    r2 = coach.ask("드라이버 그립은 어떻게 잡아야 하나요?")
    coach.ask("퍼팅할 때 손목은 어떻게 써야 하나요?", model=haiku)

    assert r1["model"] == haiku and r2["model"] == config.MODEL_ID
    assert made[haiku].calls == 2 and made[config.MODEL_ID].calls == 1


def test_injected_llm_ignores_model_choice(agent):
    r = agent.ask("드라이버 그립은 어떻게 잡아야 하나요?", model="us.amazon.nova-lite-v1:0")
    assert r["model"] == "FakeCoachLLM" and agent.llm.calls == 1


def test_follow_up_without_golf_words_passes_inside_a_thread(agent):
    first = agent.ask("퍼팅할 때 손목은 어떻게 써야 하나요?", thread_id="t-follow")
    assert first["status"] == config.STATUS_ANSWERED
    r = agent.ask("그럼 하루에 몇 번 하면 돼요?", thread_id="t-follow")
    assert r["status"] != config.STATUS_BLOCKED
    fresh = agent.ask("그럼 하루에 몇 번 하면 돼요?", thread_id="t-new")
    assert fresh["status"] == config.STATUS_NO_ANSWER and fresh["issue_codes"] == []


def test_off_topic_question_ends_as_no_answer_without_search_or_llm(agent):
    calls = agent.llm.calls
    r = agent.ask("오늘 서울 날씨 어때요?")
    assert r["status"] == config.STATUS_NO_ANSWER
    assert r["issue_codes"] == [] and r["sources"] == []
    assert r["answer"] == f"{config.NO_ANSWER_TEXT} {config.OFF_TOPIC_SUFFIX}"
    assert [t["step"] for t in r["trace"]] == ["guard"] and agent.llm.calls == calls


def test_posture_follow_up_after_photo_uses_the_photo_source(agent):
    """사진 판정 뒤 '척추는 어떻게 고쳐요?'처럼 검색에 안 걸리는 자세 질문은 직전 판정 근거(address.md)로 답합니다."""
    agent.ask("제 어드레스 자세 봐주세요", thread_id="t-photo", pose_fixture=load_fixture("photo_spine_bad"))
    r = agent.ask("무릎은 괜찮은데 척추는 어떻게 고쳐요?", thread_id="t-photo")
    assert r["status"] == config.STATUS_ANSWERED and r["sources"] == ["address.md"]
    alone = agent.ask("무릎은 괜찮은데 척추는 어떻게 고쳐요?", thread_id="t-alone")
    assert alone["status"] == config.STATUS_NO_ANSWER
