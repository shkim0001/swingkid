"""검색: 8개 주제 질문은 맞는 문서를 찾고, 문서에 없는 질문은 빈 목록(근거 없음)을 돌려준다."""

import json

import pytest

from src.shared import config
from src.knowledge.retriever import BM25Retriever, load_chunks


@pytest.fixture(scope="module")
def retriever():
    return BM25Retriever()


def test_docs_are_marked_as_dummy_and_cover_topics():
    names = {p.name for p in config.DOCS_DIR.glob("*.md")}
    topics = {"grip.md", "address.md", "driver.md", "iron.md", "short_game.md", "putting.md", "rules.md", "etiquette.md"}
    assert topics <= names
    assert len(names) >= 8
    for path in config.DOCS_DIR.glob("*.md"):
        assert "교육용 더미 데이터입니다" in path.read_text(encoding="utf-8"), path.name
    assert "2023년판" in (config.DOCS_DIR / "rules.md").read_text(encoding="utf-8")
    for name in ("address.md", "backswing.md"):
        assert "교육용 기준값" in (config.DOCS_DIR / name).read_text(encoding="utf-8")


def test_chunks_are_small():
    assert all(len(c.text) <= 800 for c in load_chunks())


def test_calibration_questions(retriever):
    """보정용 질문(평가셋과 별개)으로 기준값이 답할 질문과 모를 질문을 가르는지 확인."""
    cal = json.loads(config.CALIBRATION_FILE.read_text(encoding="utf-8"))
    for item in cal["answerable"]:
        hits = retriever.search(item["q"])
        assert hits and hits[0].doc_id == item["doc"], item["q"]
    for q in cal["unanswerable"]:
        assert retriever.search(q) == [], q


@pytest.mark.parametrize(
    "question",
    [
        "오늘 라운드 전에 스트레칭은 어떻게 해요?",  # '오늘'은 최신 정보 질문의 표시가 아님
        "스코어 기록은 어떻게 해요?",              # '기록'도 마찬가지
        "퍼팅 연습 방법 추천해 주세요",
        "메이저 대회에는 어떤 게 있어요?",           # 대회 목록은 문서에 있음
        "평균 타수로 핸디캡을 정할 수 있나요?",
    ],
)
def test_fresh_info_rule_keeps_normal_questions(question):
    from src.knowledge.retriever import needs_fresh_info

    assert not needs_fresh_info(question)


def test_chroma_sync_reindexes_only_changed_chunks():
    """라이브 색인은 문서가 바뀌면 바뀐 조각만 다시 넣고, 없어진 조각은 지웁니다."""
    pytest.importorskip("langchain_chroma")
    import uuid

    from langchain_core.embeddings import DeterministicFakeEmbedding

    from src.knowledge.retriever import ChromaRetriever
    from src.knowledge.schemas import Chunk

    a, b, c = Chunk("a.md", "그립 - 왼손 너클"), Chunk("b.md", "퍼팅 - 어깨 스트로크"), Chunk("c.md", "핸디캡 - 계산")
    r = ChromaRetriever(
        embeddings=DeterministicFakeEmbedding(size=16),
        persist_dir=None,
        collection=f"test-{uuid.uuid4().hex}",
        chunks=[a, b],
    )
    assert len(r.store.get(include=[])["ids"]) == 2
    assert r.sync([a, b]) == {"added": 0, "removed": 0}
    assert r.sync([a, c]) == {"added": 1, "removed": 1}
    assert sorted(m["source"] for m in r.store.get(include=["metadatas"])["metadatas"]) == ["a.md", "c.md"]
