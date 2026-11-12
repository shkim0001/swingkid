"""test_queries.csv 스키마와 오프라인 자체 평가 결과를 확인한다."""

import csv
from collections import Counter

from src.evaluate import CSV_PATH, check_expected, run

COLUMNS = ["id", "category", "input", "expected_traits", "forbidden", "expected_tools", "note"]


def test_csv_schema():
    rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8-sig")))
    assert list(rows[0].keys()) == COLUMNS
    # 과제 기준(4-4): 최소 10건, 권장 15~20건. 이 서비스는 20건을 권장 비율대로 둡니다
    # positive 40% · negative 20% · edge 25% · guardrail 15% → 8 · 4 · 5 · 3
    assert 10 <= len(rows) <= 20
    assert Counter(r["category"] for r in rows) == {"positive": 8, "negative": 4, "edge": 5, "guardrail": 3}
    assert sum("status=no_answer" in r["expected_traits"] for r in rows) >= 2
    assert sum("status=blocked" in r["expected_traits"] for r in rows) >= 2
    assert [r["id"] for r in rows] == [str(i) for i in range(1, len(rows) + 1)]
    assert set(Counter(r["category"] for r in rows)) <= {"positive", "negative", "edge", "guardrail"}
    assert all(r["input"].strip() and r["expected_traits"].strip() for r in rows)


def test_multi_agent_atoms():
    web = {"sources": ["https://www.lpga.com/stats"], "trace": [{"step": "info_agent"}]}
    doc = {"sources": ["rules.md"], "trace": [{"step": "retrieve"}]}
    assert check_expected("sources에 웹 출처(URL) 포함", web) is True
    assert check_expected("sources에 웹 출처(URL) 포함", doc) is False
    assert check_expected("trace에 info_agent 단계 포함", web) is True
    assert check_expected("trace에 info_agent 단계 포함", doc) is False


def test_offline_self_evaluation_passes():
    results = run()
    failed = [r.id for r in results if not r.passed]
    assert not failed, f"실패한 케이스: {failed}"


def test_guardrail_stress_set_passes_offline():
    """표현을 바꾼 공격 12건 + 오탐 확인 6건(evaluation/guardrail_stress.csv)이 모두 통과해야 합니다."""
    from src import config
    from src.evaluate import run

    results = run(csv_path=config.PROJECT_ROOT / "evaluation" / "guardrail_stress.csv")
    assert len(results) == 18
    assert [r.id for r in results if not r.passed] == []
