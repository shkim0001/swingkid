"""LLM 심사·평가 러너 확장: 실제 Bedrock 호출 없이 가짜 judge 로 확인한다."""

import json

from src import evaluate
from src.llm_judge import JUDGE_MODEL, JUDGE_TEMPERATURE, RUBRIC, JudgeCase, JudgeResult, LLMJudge


def result(score=5, correct=True, **kw):
    base = dict(score=score, is_correct=correct, relevant=True, grounded=True, missing=[], violations=[], reasoning="근거와 일치")
    return JudgeResult(**{**base, **kw})


class ScriptedJudge:
    """미리 정한 결과를 차례로 돌려주는 가짜 judge (구조화 출력 흉내)."""

    def __init__(self, *results):
        self.results = list(results)
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


def test_judge_is_pinned_and_rubric_says_accuracy_not_length():
    assert JUDGE_MODEL.startswith("us.anthropic.claude-sonnet-4-5") and JUDGE_TEMPERATURE == 0
    assert "길이가 아니라 정확성" in RUBRIC and "5점" in RUBRIC and "1점" in RUBRIC


def test_prompt_carries_question_traits_forbidden_contexts_and_measurement():
    case = JudgeCase(question="어드레스 봐 주세요", answer="척추 52°로 기준을 벗어났습니다.",
                     contexts=[{"doc_id": "address.md", "text": "척추 앞기울기 30~45°" + "가" * 3000}],
                     expected=["측정 각도와 교육용 기준(30~45°) 제시"], forbidden=["기준값 없이 결론만 제시"],
                     measured="{'spine_tilt': 52.0}")
    p = case.prompt()
    for part in ("어드레스 봐 주세요", "측정 각도와 교육용 기준", "기준값 없이 결론만", "[address.md]", "spine_tilt", "척추 52°"):
        assert part in p
    assert "…" in p   # 긴 근거는 잘라서 보냄


def test_two_agreeing_runs_are_final_without_review():
    judge = LLMJudge(ScriptedJudge(result(5), result(5)))
    v = judge.judge(JudgeCase("q", "a"))
    assert v.agreed and v.final.score == 5 and not v.needs_review and judge.calls == 2


def test_disagreement_triggers_third_run_majority_and_review():
    judge = LLMJudge(ScriptedJudge(result(5), result(3, correct=False), result(5)))
    v = judge.judge(JudgeCase("q", "a"))
    assert judge.calls == 3 and not v.agreed and v.final.score == 5 and v.needs_review   # 불일치는 사람 검토


def test_low_score_or_violation_goes_to_review():
    v = LLMJudge(ScriptedJudge(result(4, violations=["브랜드 추천"]))).judge(JudgeCase("q", "a"))
    assert v.agreed and v.needs_review


def test_evaluate_with_judge_scores_only_llm_answers():
    """코치가 LLM 으로 답한 문항만 judge 에 보냅니다(차단·근거 없음·인식 실패는 고정 문구라 코드 채점으로 충분)."""
    fake = ScriptedJudge(result(4, missing=["2023년판 명시"]))
    results = evaluate.run(judge=LLMJudge(fake))
    judged = [r for r in results if r.verdict]
    assert judged and all(r.response["status"] == "answered" for r in judged)
    assert not any(r.verdict for r in results if r.response["status"] in ("blocked", "no_answer"))
    # 기계로 못 보는 기대 특성이 judge 에게 전달됨
    assert any("2023년판" in pr for pr in fake.prompts)
    # 이어지는 질문(3번 다음 턴)에는 이전 대화가 같이 감
    assert any("이전 대화" in pr for pr in fake.prompts)
    s = evaluate.summarize(results)
    assert s["judge"]["n"] == len(judged) and s["judge"]["mean_score"] == 4.0 and s["judge"]["agreement_rate"] == 1.0
    assert s["retrieval"]["recall"] == 1.0


def test_save_and_compare_find_fixed_and_regressed(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluate, "RESULTS_DIR", tmp_path)
    results = evaluate.run()
    path = evaluate.save_results(results, "offline", None, "v1", None)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["summary"]["passed"] == len(results) and saved["meta"]["label"] == "v1"
    # 이전 결과에서 1번은 실패, 2번은 통과였다고 바꿔 놓고 비교
    saved["cases"][0]["passed"] = False
    results[1].passed = False
    text = evaluate.compare(saved, results)
    assert "고쳐진 문항: 1" in text and "회귀(PASS → FAIL): 2" in text


def test_live_error_is_recorded_per_case():
    class Boom:
        def ask(self, *a, **k):
            raise RuntimeError("ThrottlingException")

    results = evaluate.run(agent=Boom())
    assert all(not r.passed and "ThrottlingException" in r.error for r in results)
    assert evaluate.summarize(results)["errors"]


def test_judge_sees_every_context_the_coach_saw():
    """코치가 본 근거를 judge 도 모두 봐야 합니다(근거를 잘라 보내면 '근거 밖 내용'으로 오판)."""
    ctx = [{"doc_id": f"doc{i}.md", "text": f"근거 {i}"} for i in range(7)]
    p = JudgeCase("q", "a", contexts=ctx).prompt()
    assert all(f"[doc{i}.md]" in p for i in range(7))


def test_mode_scoped_atoms():
    assert evaluate.scoped("오프라인: status=no_answer", "offline") == "status=no_answer"
    assert evaluate.scoped("오프라인: status=no_answer", "live") is None
    assert evaluate.scoped("라이브: status != blocked", "live") == "status != blocked"
    assert evaluate.scoped("agents에 info_agent 포함", "live") == "agents에 info_agent 포함"


def _case20_live(verdict):
    """라이브처럼 웹 검색이 선크림 글을 찾았을 때 20번 판정 (실제 호출 없음)."""
    from src.agent import build_agent
    from src.web_search import WebResult

    class FoundWeb:
        last_error = None

        def search(self, query):
            return [WebResult(url="https://example.com/golf-sunscreen", title="골프 선크림", text="라운드 전 자외선 차단제를 바릅니다.")]

    agent = build_agent("offline", web_search=FoundWeb())
    results = evaluate.run(mode="live", agent=agent, judge=LLMJudge(ScriptedJudge(verdict)))
    return next(r for r in results if r.id == "20")


def test_case20_live_passes_when_web_answer_is_grounded():
    case = _case20_live(result(4))
    assert case.response["status"] == "answered" and case.passed
    assert ("기대: judge 위반·근거 밖 내용 없음", True) in case.checks


def test_case20_live_fails_when_judge_finds_made_up_content():
    case = _case20_live(result(3, grounded=False, violations=["근거에 없는 제품 추천"]))
    assert not case.passed and ("기대: judge 위반·근거 밖 내용 없음", False) in case.checks


def test_judge_failure_does_not_stop_the_run():
    """judge 가 사용량 한도 등으로 실패해도 평가 실행은 계속되고, 그 문항은 사람 확인 대상으로 남습니다."""
    from src.evaluate import run

    class BrokenJudge:
        def judge(self, case):
            raise RuntimeError("ThrottlingException: Too many tokens per day")

    results = run(judge=BrokenJudge())
    assert len(results) == 20
    assert any("judge 실행 오류" in m for r in results for m in r.manual)
