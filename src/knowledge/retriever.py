"""RAG 파이프라인 — 골프 지식 문서 검색.

검색기는 두 가지이고, 둘 다 같은 메서드 `search(query) -> list[Chunk]`를 가집니다.
에이전트는 어느 검색기가 들어왔는지 모르고 이 메서드만 부릅니다(주입 가능 구조).

1. BM25Retriever  — 자동 채점·테스트용. 실제 API 호출 없이 실제 더미 문서를 검색합니다.
   한국어는 조사가 붙어 공백으로 자르면 잘 맞지 않으므로 kiwipiepy 형태소 분석으로 토큰을 만듭니다.
2. ChromaRetriever — 라이브 시연용. 고정 스택(Titan v2 임베딩 + Chroma)으로 검색합니다.

두 검색기 모두 '근거 없음'을 스스로 판단합니다. 임계값을 넘는 조각이 하나도 없으면 빈 목록을 돌려주고,
에이전트는 빈 목록을 받으면 LLM을 부르지 않고 고정 no_answer 문구로 답합니다.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from src import config


@dataclass(frozen=True)
class Chunk:
    """검색 결과 한 조각. doc_id는 파일명(예: address.md)입니다."""

    doc_id: str
    text: str
    score: float = 0.0


# ── 문서 읽기와 조각내기 ────────────────────────────────────


def load_chunks(docs_dir: Path = config.DOCS_DIR, max_chars: int = 800) -> list[Chunk]:
    """마크다운 문서를 '## 소제목' 단위로 자릅니다.

    각 조각 앞에 문서 제목(# 줄)을 붙여, 조각만 봐도 어느 주제인지 알 수 있게 합니다.
    한 조각이 max_chars를 넘으면 문단 경계에서 한 번 더 자릅니다(chunk_size 800 이하 규약).
    """
    chunks: list[Chunk] = []
    for path in sorted(docs_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        title_match = re.search(r"^# (.+)$", text, flags=re.M)
        title = title_match.group(1).strip() if title_match else path.stem
        # '## '로 시작하는 줄을 기준으로 나눕니다. 첫 덩어리(제목·더미 표기)는 건너뜁니다.
        sections = re.split(r"^## ", text, flags=re.M)[1:]
        for section in sections:
            body = f"{title} - {section.strip()}"
            for piece in _split_long(body, max_chars):
                chunks.append(Chunk(doc_id=path.name, text=piece))
    return chunks


def _split_long(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    pieces, current = [], ""
    for para in text.split("\n"):
        if current and len(current) + len(para) + 1 > max_chars:
            pieces.append(current)
            current = ""
        current = f"{current}\n{para}" if current else para
    if current:
        pieces.append(current)
    return pieces


# ── 한국어 토큰화 ───────────────────────────────────────────

# 검색에 의미가 있는 품사만 남깁니다: 일반·고유명사, 외국어(OB 등), 숫자, 어근, 동사·형용사 어간.
_KEEP_TAGS = {"NNG", "NNP", "SL", "SN", "XR", "VV", "VA"}
# 어느 질문에나 나오는 말이라 점수만 부풀리는 단어입니다.
# "골프"·"라운드"는 모든 문서 주제에 걸치는 말이라 검색 근거로 쓰지 않습니다.
_STOPWORDS = {"하", "되", "있", "없", "같", "알리", "주", "보", "어떻", "방법", "때", "것", "수", "좀", "거", "법", "골프", "라운드"}


# 형태소 분석기가 문장마다 다르게 자르는 골프 복합어입니다(예: 질문의 '분실구는'은 '분실+구', 문서의 '분실구'는 한 단어).
# 사용자 사전에 넣어 질문과 문서가 같은 토큰을 갖게 합니다.
_GOLF_TERMS = (
    "분실구", "잠정구", "오구", "볼마커", "그늘집", "홀인원", "티샷", "벙커샷", "칩샷", "피치샷", "어프로치",
    "다운블로", "팔로스루", "스카이볼", "언플레이어블", "스테이블포드", "레이업", "도그레그", "스크린골프",
    "페어웨이", "프리샷", "핸디캡", "피치마크", "그린피", "티오프", "유틸리티", "하이브리드", "샌드웨지", "피칭웨지",
)


@lru_cache(maxsize=1)
def _kiwi():
    from kiwipiepy import Kiwi  # 무거운 객체라 처음 쓸 때 한 번만 만듭니다.

    kiwi = Kiwi()
    for term in _GOLF_TERMS:
        kiwi.add_user_word(term, "NNP")
    return kiwi


def tokenize(text: str) -> list[str]:
    tokens = []
    for token in _kiwi().tokenize(text):
        tag = token.tag.split("-")[0]
        if tag not in _KEEP_TAGS:
            continue
        form = token.form.lower()
        if form in _STOPWORDS:
            continue
        tokens.append(form)
    return tokens


# ── 문서 범위 밖 질문 ───────────────────────────────────────

# 지식 문서는 시간이 지나도 변하지 않는 내용(기술·규칙·용어·역사 개요)만 담습니다.
# 최신 대회 결과·순위·선수 기록·시세·장소 추천을 묻는 질문은 '메이저 대회', '골프장' 같은 단어가
# 문서와 겹쳐도 문서로 답할 수 없으므로 근거 없음으로 봅니다. (멀티 agent 전환 후에는 골프 정보 agent 가 웹에서 찾습니다)
# '오늘·현재·기록'처럼 일반 질문에도 흔한 단어는 넣지 않습니다. 예) "오늘 라운드 전 스트레칭", "스코어 기록하는 법"
_FRESH_INFO = re.compile(
    r"올해|작년|지난\s*(해|시즌|대회)|이번\s*(시즌|대회|주)|최근|요즘|최신"
    r"|순위|랭킹|우승자|누가\s*우승|몇\s*번\s*우승|몇\s*승|우승\s*횟수"
    r"|(선수|프로)\S*\s.{0,15}(평균|성적|상금|통산)"
    r"|시세|회원권|유튜버|유튜브|예능|맛집"
    r"|(골프장|코스|연습장)\S*\s.{0,10}(어디|추천)"
)


def needs_fresh_info(query: str) -> bool:
    """문서로는 답할 수 없는 최신·기록·시세·장소 추천 질문인지 봅니다."""
    return bool(_FRESH_INFO.search(query))


# ── 검색기 ──────────────────────────────────────────────────


class BM25Retriever:
    """자동 채점용 결정적 검색기. 같은 질문에는 항상 같은 결과를 냅니다."""

    def __init__(
        self,
        chunks: list[Chunk] | None = None,
        min_score: float = config.BM25_MIN_SCORE,
        min_coverage: float = config.BM25_MIN_COVERAGE,
        top_k: int = config.TOP_K,
    ):
        from rank_bm25 import BM25Okapi

        self.chunks = chunks if chunks is not None else load_chunks()
        self.min_score = min_score
        self.min_coverage = min_coverage
        self.top_k = top_k
        self._chunk_tokens = [tokenize(c.text) for c in self.chunks]
        self._bm25 = BM25Okapi(self._chunk_tokens)
        # 단어 희귀도(IDF). 문서에 한 번도 안 나온 단어(df=0)가 가장 큽니다.
        df = Counter(t for tokens in self._chunk_tokens for t in set(tokens))
        n = len(self.chunks)
        self._idf = lambda t: math.log((n + 1) / (df.get(t, 0) + 0.5))
        # 문서별 토큰 모음. 단어 비율은 1위 조각이 속한 '문서 전체'로 봅니다.
        # ('머리 올리는 날 준비물'처럼 핵심어가 같은 문서의 다른 소제목에 나뉘어 있어도 근거로 인정)
        self._doc_tokens: dict[str, set[str]] = {}
        for chunk, tokens in zip(self.chunks, self._chunk_tokens):
            self._doc_tokens.setdefault(chunk.doc_id, set()).update(tokens)

    def scores(self, query: str) -> list[float]:
        tokens = tokenize(query)
        if not tokens:
            return [0.0] * len(self.chunks)
        return [float(s) for s in self._bm25.get_scores(tokens)]

    def coverage(self, query: str, chunk_index: int) -> float:
        """질문 단어가 그 조각이 속한 문서에 얼마나 들어 있는지를 희귀도(IDF) 가중 비율로 봅니다.

        '치다·좋다·선수·근처'처럼 흔한 단어만 겹치고 핵심어('연봉', '맛집')가 문서에 없으면
        단순 개수 비율은 높아도 가중 비율은 낮아져 근거 없음으로 판단됩니다.
        """
        q = tokenize(query)
        total = sum(self._idf(t) for t in q)
        if not total:
            return 0.0
        doc = self._doc_tokens[self.chunks[chunk_index].doc_id]
        return sum(self._idf(t) for t in q if t in doc) / total

    def search(self, query: str) -> list[Chunk]:
        """근거가 충분하면 점수 순으로 top_k개 조각을, 아니면 빈 목록을 돌려줍니다.

        BM25 점수만 보면 '라운드', '드라이버' 같은 흔한 단어 하나로도 점수가 오르므로,
        1위 조각이 (1) 점수 하한을 넘고 (2) 질문 단어를 일정 비율 이상 담고 있을 때만 근거로 인정합니다.
        최신 결과·순위·시세 같은 질문(needs_fresh_info)은 검색하지 않고 바로 근거 없음입니다.
        """
        if needs_fresh_info(query):
            return []
        scores = self.scores(query)
        order = sorted(range(len(self.chunks)), key=lambda i: scores[i], reverse=True)
        best = order[0]
        if scores[best] < self.min_score or self.coverage(query, best) < self.min_coverage:
            return []
        hits = [Chunk(self.chunks[i].doc_id, self.chunks[i].text, round(scores[i], 3)) for i in order if scores[i] >= self.min_score]
        return hits[: self.top_k]


class ChromaRetriever:
    """라이브 시연용 검색기. Titan v2 임베딩 + Chroma(고정 스택)를 씁니다.

    실제 Bedrock 임베딩 호출이 일어나므로 자동 채점에서는 쓰지 않습니다.
    만들 때마다 data/docs 와 색인을 맞춥니다(sync). 조각 ID 가 '파일명#본문 해시'라서
    문서를 추가·수정·삭제하면 바뀐 조각만 새로 임베딩하고, 없어진 조각은 지웁니다.
    """

    def __init__(
        self,
        embeddings=None,
        persist_dir: str | None = config.PERSIST_DIR,
        collection: str = config.COLLECTION,
        min_relevance: float = config.CHROMA_MIN_RELEVANCE,
        top_k: int = config.TOP_K,
        chunks: list[Chunk] | None = None,
    ):
        from langchain_chroma import Chroma

        if embeddings is None:
            from langchain_aws import BedrockEmbeddings

            embeddings = BedrockEmbeddings(model_id=config.EMBED_MODEL_ID, region_name=config.REGION)
        self.store = Chroma(
            collection_name=collection,
            embedding_function=embeddings,
            persist_directory=persist_dir,
        )
        self.min_relevance = min_relevance
        self.top_k = top_k
        self.sync(chunks)

    @staticmethod
    def chunk_id(chunk: Chunk) -> str:
        return f"{chunk.doc_id}#{hashlib.sha1(chunk.text.encode('utf-8')).hexdigest()[:16]}"

    def sync(self, chunks: list[Chunk] | None = None) -> dict[str, int]:
        """색인을 문서와 맞춥니다. 추가·삭제한 조각 수를 돌려줍니다."""
        wanted = {self.chunk_id(c): c for c in (chunks if chunks is not None else load_chunks())}
        have = set(self.store.get(include=[])["ids"])
        stale = sorted(have - wanted.keys())
        new = [cid for cid in wanted if cid not in have]
        if stale:
            self.store.delete(ids=stale)
        if new:
            self.store.add_texts(
                texts=[wanted[cid].text for cid in new],
                metadatas=[{"source": wanted[cid].doc_id} for cid in new],
                ids=new,
            )
        return {"added": len(new), "removed": len(stale)}

    def search(self, query: str) -> list[Chunk]:
        if needs_fresh_info(query):
            return []
        # 관련도 = 1 - L2 거리/√2 (langchain 의 기본 계산과 같음). 직접 계산해 '0~1 범위 밖' 경고가 로그를 덮지 않게 합니다.
        results = self.store.similarity_search_with_score(query, k=self.top_k)
        scored = [(doc, 1.0 - distance / math.sqrt(2)) for doc, distance in results]
        return [
            Chunk(doc.metadata.get("source", "unknown"), doc.page_content, round(score, 3))
            for doc, score in scored
            if score >= self.min_relevance
        ]


class HybridRetriever:
    """라이브용: 단어 검색(BM25) + 의미 검색(Chroma)을 합칩니다.

    Titan 의미 검색은 '버디가 뭐예요?'처럼 짧은 용어 질문의 점수가 낮아 근거를 놓치고,
    BM25 는 표현이 달라지면 놓칩니다. 둘 다 스스로 '근거 없음'을 판단하므로, 합친 결과는
    한쪽이라도 근거를 찾으면 쓰고 둘 다 못 찾으면 빈 목록입니다. 용어가 정확히 맞는 BM25 결과를 먼저 둡니다.
    """

    def __init__(self, semantic, lexical, top_k: int = config.TOP_K + 1):
        self.semantic = semantic
        self.lexical = lexical
        self.top_k = top_k

    def search(self, query: str) -> list[Chunk]:
        merged: list[Chunk] = []
        seen: set[str] = set()
        for chunk in [*self.lexical.search(query), *self.semantic.search(query)]:
            if chunk.text not in seen:
                seen.add(chunk.text)
                merged.append(chunk)
        return merged[: self.top_k]


def load_doc_text(doc_id: str) -> str:
    """문서 전체 본문을 읽습니다. 미디어 경로에서 근거 문서를 contexts에 넣을 때 씁니다."""
    return (config.DOCS_DIR / doc_id).read_text(encoding="utf-8")
