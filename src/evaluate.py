"""자체 평가 러너 — evaluation/test_queries.csv 를 돌려 채점합니다. (계획: evaluation/EVAL_PLAN.md)

세 겹으로 봅니다.
  A. 정답셋 기반(코드): status·issue_codes·sources·agents·trace 등 답이 정해진 항목 — 아래 '판정 방법'
  B. LLM 심사(--judge): 기계로 못 보는 기대 특성·금지 항목과 답변 품질 — src/llm_judge.py (고정 루브릭·고정 모델)
  C. RAG 진단: 검색 재현율(기대 문서를 가져왔나)·정밀도(몇 번째였나, 1위 비율·MRR)는 코드로,
     충실성(근거 밖 내용 없음)·답변 적합성(질문에 바로 답함)은 judge 로 계산합니다(RAGAS 4대 지표의 정의를 따름).

모드
  오프라인(기본): 가짜 모델 + BM25 + 가짜 웹 검색기 + 관절 좌표 fixture. 실제 API 호출이 없고 결과가 매번 같습니다.
  라이브(--live): Bedrock Claude · Titan/Chroma+BM25 · MCP 웹 검색. 대화 기록은 메모리에만 두어 실제 대화 DB 를 건드리지 않습니다.

판정 방법
- expected_traits / forbidden 은 사람이 읽는 문장이지만, 그중 기계로 확인할 수 있는 조각만 골라 채점합니다.
  예) "status=answered", "issue_codes에 X 포함", "sources=[address.md]", "guard_ 코드 없음",
      "answer가 '...'로 시작", "X 오검출", "LLM·검색 호출" 등
  멀티 agent용: "sources에 웹 출처(URL) 포함"(http/https 로 시작하는 출처), "agents에 info_agent 포함"(supervisor 가 부른 sub-agent),
      "trace에 X 단계 포함"(도구 단계: pose·retrieve·web_search·coach)
- 기계로 확인할 수 없는 조각(톤, 설명 품질 등)은 '수동 확인'으로 모아 두고, --judge 면 LLM 심사에 넘깁니다.
- 조각 앞에 '오프라인:' 또는 '라이브:'를 붙이면 그 모드에서만 봅니다. 라이브 웹 검색처럼 실행마다 바뀌는 환경에 맞춘 기대값용입니다.
  예) 오프라인(가짜 웹 검색기는 결과 없음): '오프라인: status=no_answer' / 라이브: '라이브: judge 위반·근거 밖 내용 없음'
- 'judge 위반·근거 밖 내용 없음'은 judge 결과로 판정합니다(답하지 않았으면 통과, judge 를 안 돌렸으면 수동 확인).
- 한 케이스는 기계로 확인한 조각이 모두 맞아야 통과입니다.

입력 규칙
- input 이 "[... fixture: 이름] 질문" 이면 data/fixtures/이름.json 을 pose_fixture 로 주입합니다.
- note 에 "N번 케이스 다음 턴" 이 있으면 N번 케이스와 같은 thread_id 로 이어서 보냅니다.

실행: python -m src.evaluate                         (오프라인 코드 채점, 요약)
      python -m src.evaluate --detail                (케이스별 판정 근거까지)
      python -m src.evaluate --live --judge --save --label v1   (라이브 + LLM 심사, 결과 저장)
      python -m src.evaluate --live --judge --save --label v2 --compare evaluation/results/<v1 파일>.json  (전후 비교)
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from src import config
from src.agent import build_agent

CSV_PATH = config.PROJECT_ROOT / "evaluation" / "test_queries.csv"
RESULTS_DIR = config.PROJECT_ROOT / "evaluation" / "results"
SWING_CODES = {config.SPINE_TILT, config.KNEE_FLEX, config.BENT_LEAD_ARM}
ALL_CODES = SWING_CODES | {config.POSE_NOT_DETECTED, config.GUARD_MEDICAL, config.GUARD_PURCHASE, config.GUARD_OFF_TOPIC}


@dataclass
class CaseResult:
    id: str
    category: str
    input: str
    passed: bool
    checks: list[tuple[str, bool]] = field(default_factory=list)   # (설명, 통과 여부)
    manual: list[str] = field(default_factory=list)                 # 수동 확인 항목
    response: dict = field(default_factory=dict)
    verdict: dict | None = None                                     # LLM 심사 결과(--judge, answered 답변만)
    retrieval: dict | None = None                                   # 검색 진단 {expected, sources, rank}
    error: str | None = None                                        # 라이브 실행 중 오류


def _split(cell: str) -> list[str]:
    return [p.strip() for p in (cell or "").split(";") if p.strip()]


def _list_literal(text: str) -> list[str]:
    inner = text.strip()[1:-1]
    return [x.strip().strip("'\"") for x in inner.split(",") if x.strip().strip("'\"")]


JUDGE_ATOM = "judge 위반·근거 밖 내용 없음"


def scoped(atom: str, mode: str) -> str | None:
    """'오프라인:'·'라이브:' 가 붙은 조각은 그 모드에서만 씁니다. 다른 모드면 None."""
    if m := re.match(r"^(오프라인|라이브):\s*(.+)$", atom):
        return m.group(2) if (m.group(1) == "오프라인") == (mode == "offline") else None
    return atom


def check_judge(r: dict, verdict: dict | None) -> bool | None:
    """judge 기준 '위반·근거 밖 내용 없음'. 답하지 않았으면 통과, judge 결과가 없으면 판단 불가."""
    if r["status"] != config.STATUS_ANSWERED:
        return True
    if verdict is None:
        return None
    return not verdict["violations"] and verdict["grounded"]


def check_expected(atom: str, r: dict) -> bool | None:
    """기대 특성 조각 하나를 응답과 비교합니다. 기계로 판단할 수 없으면 None."""
    if atom == "답했다면 sources에 웹 출처(URL) 포함":
        return r["status"] != config.STATUS_ANSWERED or any(s.startswith(("http://", "https://")) for s in r["sources"])
    if m := re.fullmatch(r"status\s*!=\s*(\w+)", atom):
        return r["status"] != m.group(1)
    if m := re.fullmatch(r"status=(\w+)", atom):
        return r["status"] == m.group(1)
    if m := re.fullmatch(r"issue_codes에 (\w+) 포함", atom):
        return m.group(1) in r["issue_codes"]
    if m := re.fullmatch(r"issue_codes=(\[.*\])", atom):
        return sorted(r["issue_codes"]) == sorted(_list_literal(m.group(1)))
    if atom == "sources에 웹 출처(URL) 포함":
        return any(s.startswith(("http://", "https://")) for s in r["sources"])
    if m := re.fullmatch(r"trace에 (\w+) 단계 포함", atom):
        return m.group(1) in {t["step"] for t in r["trace"]}
    if m := re.fullmatch(r"agents에 (\w+) 포함", atom):
        return m.group(1) in r.get("agents", [])
    if m := re.fullmatch(r"sources에 ([\w.]+) 포함", atom):
        return m.group(1) in r["sources"]
    if m := re.fullmatch(r"sources=(\[.*\])", atom):
        return r["sources"] == _list_literal(m.group(1))
    if atom.startswith("issue_codes에 guard_ 코드 없음") or atom == "guard_ 코드 없음":
        return not any(c.startswith("guard_") for c in r["issue_codes"])
    if m := re.match(r"answer가 '(.+?)'로 시작", atom):
        return r["answer"].startswith(m.group(1))
    if m := re.fullmatch(r"answer='(.+)'", atom):
        return r["answer"] == m.group(1)
    if atom == "고정 거절 문구":
        return r["answer"].startswith(config.BLOCKED_TEXT)
    if "전문가와 상담하세요" in atom:
        return config.MEDICAL_SUFFIX in r["answer"]
    return None


def check_forbidden(atom: str, r: dict) -> bool | None:
    """금지 조각 하나를 확인합니다. 위반이 없으면 True, 위반이면 False, 판단 불가면 None."""
    steps = {t["step"] for t in r["trace"]}
    codes = set(r["issue_codes"])
    named = [c for c in ALL_CODES if c in atom]
    if named and ("오검출" in atom or "차단" in atom or atom.strip() in ALL_CODES):
        return not (set(named) & codes)
    if atom.startswith("결함 코드 오검출") or atom == "결함 코드 출력":
        return not (codes & SWING_CODES)
    if atom == "sources 빈 목록":
        return bool(r["sources"])
    if "차단(blocked) 처리" in atom:
        return r["status"] != config.STATUS_BLOCKED
    if "info_agent" in atom and "호출" in atom:
        return config.INFO_AGENT not in r.get("agents", []) and "web_search" not in steps
    if atom.startswith("LLM·검색 호출"):
        return not ({"retrieve", "web_search", "coach"} & steps)
    if atom.startswith("guard_ 코드 2개 이상"):
        return sum(c.startswith("guard_") for c in codes) < 2
    if "영상을 LLM에 직접 전달" in atom:
        return "pose" in steps   # 영상은 반드시 포즈 단계를 거쳐 수치로 바뀐 뒤 코칭됩니다
    return None


def parse_input(text: str) -> tuple[str, dict | None]:
    m = re.match(r"^\[[^\]]*fixture:\s*(\w+)\]\s*(.*)$", text)
    if not m:
        return text, None
    fixture = json.loads((config.FIXTURES_DIR / f"{m.group(1)}.json").read_text(encoding="utf-8"))
    return m.group(2), fixture


def _expected_docs(row: dict) -> list[str]:
    """기대 특성에서 기대 문서(.md)를 뽑습니다. 검색 진단(재현율·정밀도)에 씁니다."""
    docs = []
    for atom in _split(row["expected_traits"]):
        if m := re.fullmatch(r"sources에 ([\w.]+\.md) 포함", atom):
            docs.append(m.group(1))
        elif m := re.fullmatch(r"sources=(\[.*\])", atom):
            docs += [d for d in _list_literal(m.group(1)) if d.endswith(".md")]
    return docs


def _build(mode: str):
    if mode == "offline":
        return build_agent("offline")
    # 라이브: 평가용 대화가 실제 대화 DB(checkpoints.sqlite)에 쌓여 다음 평가에 섞이지 않도록 메모리 체크포인터를 씁니다
    from langgraph.checkpoint.memory import InMemorySaver

    return build_agent("live", checkpointer=InMemorySaver())


def run(detail: bool = False, mode: str = "offline", judge=None, model: str | None = None, agent=None) -> list[CaseResult]:
    agent = agent or _build(mode)
    rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8-sig")))
    threads: dict[str, str] = {}
    asked: dict[str, tuple[str, str]] = {}          # id → (질문, 답변): 이어지는 질문의 이전 대화
    results: list[CaseResult] = []
    for row in rows:
        question, fixture = parse_input(row["input"])
        thread = f"case-{row['id']}"
        prior = None
        if m := re.search(r"(\d+)번 케이스 다음 턴", row.get("note", "")):
            thread = threads.get(m.group(1), thread)
            prior = asked.get(m.group(1))
        threads[row["id"]] = thread
        try:
            r = agent.ask(question, pose_fixture=fixture, thread_id=thread, model=model)
        except Exception as exc:   # 라이브 오류(사용량 한도·네트워크)는 그 문항만 실패로 남기고 계속합니다
            results.append(CaseResult(row["id"], row["category"], row["input"], False, error=f"{type(exc).__name__}: {exc}"[:300],
                                      response={"status": "error", "issue_codes": [], "sources": [], "trace": [], "answer": "", "contexts": []}))
            continue
        asked[row["id"]] = (question, r["answer"])

        checks, manual = [], []
        manual_expected, manual_forbidden = [], []
        judge_atoms = []
        for raw in _split(row["expected_traits"]):
            atom = scoped(raw, mode)
            if atom is None:
                continue
            if atom == JUDGE_ATOM:
                judge_atoms.append(atom)
                continue
            ok = check_expected(atom, r)
            if ok is None:
                manual.append(f"기대: {atom}"); manual_expected.append(atom)
            else:
                checks.append((f"기대: {atom}", ok))
        for raw in _split(row.get("forbidden", "")):
            atom = scoped(raw, mode)
            if atom is None:
                continue
            ok = check_forbidden(atom, r)
            if ok is None:
                manual.append(f"금지: {atom}"); manual_forbidden.append(atom)
            else:
                checks.append((f"금지: {atom}", ok))
        passed = all(ok for _, ok in checks) and bool(checks)
        case = CaseResult(row["id"], row["category"], row["input"], passed, checks, manual, r)

        steps = {s["step"] for s in r["trace"]}
        docs = _expected_docs(row)
        if docs and "retrieve" in steps:
            ranks = [r["sources"].index(d) + 1 if d in r["sources"] else None for d in docs]
            case.retrieval = {"expected": docs, "sources": r["sources"], "rank": min((x for x in ranks if x), default=None)}

        if judge is not None and r["status"] == config.STATUS_ANSWERED and "coach" in steps:
            from src.llm_judge import JudgeCase

            pose = next((s["output"] for s in r["trace"] if s["step"] == "pose"), "")
            verdict = judge.judge(JudgeCase(
                question=question, answer=r["answer"], contexts=r["contexts"],
                expected=manual_expected, forbidden=manual_forbidden, measured=pose,
                prior=f"사용자: {prior[0]}\n코치: {prior[1]}" if prior else "",
            ))
            case.verdict = verdict.to_dict()
        for atom in judge_atoms:   # judge 결과로 판정하는 조각
            ok = check_judge(r, case.verdict)
            if ok is None:
                case.manual.append(f"기대: {atom}")
            else:
                case.checks.append((f"기대: {atom}", ok))
        case.passed = all(ok for _, ok in case.checks) and bool(case.checks)
        results.append(case)
    return results


def summarize(results: list[CaseResult]) -> dict:
    total = len(results)
    out = {
        "total": total,
        "passed": sum(r.passed for r in results),
        "by_category": {},
        "errors": [r.id for r in results if r.error],
    }
    for r in results:
        c = out["by_category"].setdefault(r.category, {"passed": 0, "total": 0})
        c["total"] += 1
        c["passed"] += r.passed
    ret = [r.retrieval for r in results if r.retrieval]
    if ret:
        ranks = [x["rank"] for x in ret]
        out["retrieval"] = {
            "n": len(ret),
            "recall": round(sum(1 for k in ranks if k) / len(ret), 3),           # Context Recall: 기대 문서를 가져왔나
            "top1": round(sum(1 for k in ranks if k == 1) / len(ret), 3),        # Context Precision: 1위였나
            "mrr": round(sum(1 / k for k in ranks if k) / len(ret), 3),          # 평균 역순위
        }
    vs = [(r.id, r.verdict) for r in results if r.verdict]
    if vs:
        n = len(vs)
        out["judge"] = {
            "n": n,
            "mean_score": round(sum(v["score"] for _, v in vs) / n, 2),
            "correct_rate": round(sum(v["is_correct"] for _, v in vs) / n, 3),
            "relevant_rate": round(sum(v["relevant"] for _, v in vs) / n, 3),     # Answer Relevancy
            "grounded_rate": round(sum(v["grounded"] for _, v in vs) / n, 3),     # Faithfulness
            "violations": sum(len(v["violations"]) for _, v in vs),
            "agreement_rate": round(sum(v["agreed"] for _, v in vs) / n, 3),     # 두 번 채점 일치율
            "review": [i for i, v in vs if v["needs_review"]],
        }
    return out


def report(results: list[CaseResult], detail: bool = False, mode: str = "offline") -> str:
    lines = []
    s = summarize(results)
    total, passed = s["total"], s["passed"]
    label = "오프라인: 가짜 모델 + BM25 + 포즈 fixture" if mode == "offline" else "라이브: Bedrock · Titan/Chroma+BM25 · MCP 웹 검색 · 포즈 fixture"
    lines.append(f"자체 평가 ({label}) — {passed}/{total} 통과 ({passed / total:.0%})")
    for cat in ("positive", "negative", "edge", "guardrail"):
        c = s["by_category"].get(cat)
        if c:
            lines.append(f"  {cat:9s} {c['passed']}/{c['total']}")
    if "retrieval" in s:
        rt = s["retrieval"]
        lines.append(f"  검색 진단 ({rt['n']}문항)  재현율 {rt['recall']:.0%} · 1위 {rt['top1']:.0%} · MRR {rt['mrr']:.2f}")
    if "judge" in s:
        j = s["judge"]
        lines.append(f"  LLM 심사 ({j['n']}문항)  평균 {j['mean_score']:.2f} · 정답 {j['correct_rate']:.0%} · 적합 {j['relevant_rate']:.0%}"
                     f" · 충실 {j['grounded_rate']:.0%} · 위반 {j['violations']}건 · 재현 {j['agreement_rate']:.0%}")
        if j["review"]:
            lines.append(f"  사람 검토 대상: {', '.join(j['review'])}")
    if s["errors"]:
        lines.append(f"  실행 오류: {', '.join(s['errors'])}")
    lines.append("")
    for r in results:
        mark = "PASS" if r.passed else "FAIL"
        resp = r.response
        tail = f" judge={r.verdict['score']}{'*' if r.verdict['needs_review'] else ''}" if r.verdict else ""
        lines.append(f"[{mark}] {r.id:>2} {r.category:9s} status={resp['status']} codes={resp['issue_codes']} sources={resp['sources']}{tail}")
        if r.error:
            lines.append(f"        ! 오류: {r.error}")
        if detail or not r.passed:
            for desc, ok in r.checks:
                lines.append(f"        {'✓' if ok else '✗'} {desc}")
            if not r.verdict:
                for m in r.manual:
                    lines.append(f"        · 수동 확인: {m}")
        if r.verdict and (detail or r.verdict["needs_review"]):
            v = r.verdict
            lines.append(f"        judge {v['score']}점 (채점 {v['runs']}) · 정답 {'O' if v['is_correct'] else 'X'} · 충실 {'O' if v['grounded'] else 'X'}")
            for x in v["missing"]:
                lines.append(f"        - 빠짐: {x}")
            for x in v["violations"]:
                lines.append(f"        - 위반: {x}")
            lines.append(f"        - 이유: {v['reasoning']}")
    return "\n".join(lines)


def save_results(results: list[CaseResult], mode: str, model: str | None, label: str, judge_model: str | None) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = RESULTS_DIR / f"{stamp}_{mode}_{label}.json"
    data = {
        "meta": {"created": stamp, "mode": mode, "label": label, "model": model or config.MODEL_ID if mode == "live" else "FakeCoachLLM",
                 "judge_model": judge_model, "eval_set": CSV_PATH.name},
        "summary": summarize(results),
        "cases": [asdict(r) for r in results],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def compare(previous: dict, results: list[CaseResult]) -> str:
    """같은 평가셋으로 본 전후 비교(4-3). 고친 문항과 회귀(PASS → FAIL)를 함께 보여 줍니다."""
    before, after = previous["summary"], summarize(results)
    prev_cases = {c["id"]: c for c in previous["cases"]}
    lines = [f"전후 비교 (이전: {previous['meta'].get('label')} · {previous['meta'].get('created')})"]
    lines.append(f"  코드 채점  {before['passed']}/{before['total']} → {after['passed']}/{after['total']}")
    if "judge" in before and "judge" in after:
        b, a = before["judge"], after["judge"]
        lines.append(f"  judge 평균 {b['mean_score']:.2f} → {a['mean_score']:.2f} · 정답 {b['correct_rate']:.0%} → {a['correct_rate']:.0%}"
                     f" · 위반 {b['violations']} → {a['violations']}건")
    if "retrieval" in before and "retrieval" in after:
        b, a = before["retrieval"], after["retrieval"]
        lines.append(f"  검색 재현율 {b['recall']:.0%} → {a['recall']:.0%} · MRR {b['mrr']:.2f} → {a['mrr']:.2f}")
    fixed = [r.id for r in results if r.passed and r.id in prev_cases and not prev_cases[r.id]["passed"]]
    regressed = [r.id for r in results if not r.passed and r.id in prev_cases and prev_cases[r.id]["passed"]]
    worse = [r.id for r in results if r.verdict and (prev_cases.get(r.id) or {}).get("verdict")
             and r.verdict["score"] < prev_cases[r.id]["verdict"]["score"]]
    lines.append(f"  고쳐진 문항: {', '.join(fixed) or '없음'}")
    lines.append(f"  회귀(PASS → FAIL): {', '.join(regressed) or '없음'}")
    lines.append(f"  judge 점수가 내려간 문항: {', '.join(worse) or '없음'}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="test_queries.csv 자체 평가 (코드 채점 + 선택: 라이브 · LLM 심사 · 전후 비교)")
    parser.add_argument("--detail", action="store_true", help="케이스별 판정 근거를 모두 출력")
    parser.add_argument("--live", action="store_true", help="실제 Bedrock·검색·MCP 웹 검색으로 실행")
    parser.add_argument("--model", help="코칭 모델 ID (라이브, 기본은 config.MODEL_ID)")
    parser.add_argument("--judge", action="store_true", help="answered 답변을 LLM 심사(고정 루브릭·Sonnet 4.5·temperature 0)로 채점")
    parser.add_argument("--save", action="store_true", help="evaluation/results/ 에 결과 JSON 저장")
    parser.add_argument("--label", default="run", help="저장 파일 이름표 (예: v1, prompt-fix)")
    parser.add_argument("--compare", help="이전 결과 JSON 과 전후 비교")
    args = parser.parse_args()
    mode = "live" if args.live else "offline"
    if args.judge and not args.live:
        parser.error("--judge 는 --live 와 함께 씁니다(오프라인 가짜 모델 답변은 품질을 잴 수 없음)")
    if args.live:
        from dotenv import load_dotenv

        load_dotenv(config.PROJECT_ROOT / ".env")
    judge = None
    if args.judge:
        from src.llm_judge import JUDGE_MODEL, LLMJudge

        judge = LLMJudge()
    results = run(mode=mode, judge=judge, model=args.model)
    print(report(results, detail=args.detail, mode=mode))
    if args.compare:
        print()
        print(compare(json.loads(Path(args.compare).read_text(encoding="utf-8")), results))
    if args.save:
        path = save_results(results, mode, args.model, args.label, JUDGE_MODEL if judge else None)
        print(f"\n결과 저장: {path.relative_to(config.PROJECT_ROOT)}")
    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
