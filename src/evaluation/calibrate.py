"""검색 '근거 없음' 기준값 확인 + 검색 진단 지표 스크립트.

data/calibration.json 의 보정용 질문(평가셋과 별개)으로 현재 기준값이 '답할 수 있는 질문'과
'답할 수 없는 질문'을 잘 가르는지 보여 주고, 검색 진단 지표(RAGAS 의 검색기 쪽 정의를 따름)를 냅니다.
  - 재현율(Context Recall): 기대 문서를 근거 목록 안에 가져왔나
  - 1위 비율·MRR(Context Precision): 기대 문서가 몇 번째로 나왔나
  - 오답률: 모를 질문에 근거를 내놓은 비율
평가셋(test_queries.csv) 20건보다 크고 서로 겹치지 않아, 평가셋에만 맞춘 과적합을 잡는 데도 씁니다.

실행: python -m src.calibrate          (오프라인 BM25)
      python -m src.calibrate --live   (라이브 검색기: BM25 + Titan/Chroma 하이브리드, Titan 임베딩 호출)
"""

from __future__ import annotations

import argparse
import json

from src import config
from src.retriever import BM25Retriever


def _retriever(live: bool):
    if not live:
        return BM25Retriever(), f"오프라인 BM25 — 기준: 점수 >= {config.BM25_MIN_SCORE}, 단어 비율 >= {config.BM25_MIN_COVERAGE}"
    from dotenv import load_dotenv

    from src.retriever import ChromaRetriever, HybridRetriever

    load_dotenv(config.PROJECT_ROOT / ".env")
    return (HybridRetriever(semantic=ChromaRetriever(), lexical=BM25Retriever()),
            f"라이브 하이브리드 — BM25 + Chroma(관련도 >= {config.CHROMA_MIN_RELEVANCE})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="검색 기준값 확인 + 검색 진단 지표")
    parser.add_argument("--live", action="store_true", help="라이브 검색기(BM25 + Titan/Chroma)로 확인")
    args = parser.parse_args(argv)
    cal = json.loads(config.CALIBRATION_FILE.read_text(encoding="utf-8"))
    retriever, label = _retriever(args.live)
    errors, ranks, false_hits = 0, [], 0
    print(label)
    for item in cal["answerable"]:
        hits = retriever.search(item["q"])
        docs = [h.doc_id for h in hits]
        rank = docs.index(item["doc"]) + 1 if item["doc"] in docs else None
        ranks.append(rank)
        ok = rank == 1
        errors += not ok
        top = hits[0].doc_id if hits else "(근거 없음)"
        print(f"{'OK ' if ok else 'BAD'} 답해야 함   {top:14s} {item['q']}")
    for q in cal["unanswerable"]:
        hits = retriever.search(q)
        ok = not hits
        errors += not ok
        false_hits += not ok
        print(f"{'OK ' if ok else 'BAD'} 모른다 해야 함 {('(근거 없음)' if ok else hits[0].doc_id):14s} {q}")
    n = len(ranks)
    recall = sum(1 for r in ranks if r) / n
    top1 = sum(1 for r in ranks if r == 1) / n
    mrr = sum(1 / r for r in ranks if r) / n
    print(f"오류 {errors}건")
    print(f"검색 진단: 재현율 {recall:.0%} · 1위 {top1:.0%} · MRR {mrr:.2f} (답할 질문 {n}개) · 오답률 {false_hits}/{len(cal['unanswerable'])} (모를 질문)")
    return errors


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
