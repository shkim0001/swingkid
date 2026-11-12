"""장기 기억 — 사용자(user_id)별로 대화방(thread_id)을 넘어 남는 기억.

단기 기억과의 차이
- 단기(대화방): SqliteSaver 체크포인터가 같은 thread_id 안의 대화 이력·직전 판정을 이어 줍니다.
- 장기(사용자): 이 모듈이 LangGraph Store 에 사용자의 **자세 판정 기록**을 쌓습니다.
  새 대화방에서도 "지난번 판정이랑 비교해 줘", "내 기록 보여 줘"에 답하고, 새 판정 때 이전 기록과 비교해 설명합니다.

무엇을 저장하나
- 사진·영상 판정이 끝난 뒤의 결정적 결과만 저장합니다: 날짜, 종류(사진/영상), 문제 코드, 측정 각도.
  LLM 이 만든 문장이나 질문 원문은 저장하지 않습니다(지어낸 내용이 기억으로 굳지 않게, 개인정보도 최소로).
- user_id 가 없으면 아무것도 저장·조회하지 않습니다. GET/DELETE /memory/{user_id} 로 보고 지울 수 있습니다.

저장소: 오프라인은 InMemoryStore, 라이브는 SqliteStore(config.MEMORY_DB). 생성자에 주입할 수 있습니다.
"""

from __future__ import annotations

import datetime
import re
import uuid
from collections import Counter

from src import config

MEMORY_DOC_ID = "memory://swing-history"   # 장기 기억을 근거로 쓸 때 contexts·sources 에 남는 이름
KIND_KO = {"photo": "사진(측면 어드레스)", "video": "영상(정면 스윙)"}
METRIC_KO = {"spine_tilt": "척추 앞기울기", "knee_flex": "무릎 각도", "lead_arm": "정점 왼팔 각도"}
CODE_KO = {
    config.SPINE_TILT: "척추 기울기 기준 벗어남",
    config.KNEE_FLEX: "무릎 각도 기준 벗어남",
    config.BENT_LEAD_ARM: "백스윙 정점 왼팔 굽음",
}

# user_id: 화면에서 정한 닉네임(한글 가능) 또는 브라우저가 만든 임의 id. 띄어쓰기 없이 2~64자.
# 로그인이 아니므로 같은 닉네임을 쓰면 같은 사람으로 봅니다.
USER_ID_PATTERN = r"^[0-9A-Za-z가-힣_-]{2,64}$"

# "지난번 판정이랑 비교", "내 기록", "예전에 올린 사진" 처럼 사용자의 이전 판정을 묻는 질문.
# 같은 대화 안의 "아까 지적한 …"은 단기 기억(대화방)이 맡으므로 넣지 않습니다.
_RECALL = re.compile(
    r"지난\s*번|지난\s*(판정|기록|사진|영상|코칭|레슨)|예전에|이전\s*(판정|기록|사진|영상)|전에\s*(올린|찍은|받은|판정)"
    r"|내\s*(판정\s*)?기록|판정\s*기록|그동안|얼마나\s*(좋아|나아|달라)|좋아졌|나아졌|달라졌|변화"
)


def is_recall_question(question: str) -> bool:
    return bool(_RECALL.search(question))


def _ns(user_id: str) -> tuple[str, ...]:
    return ("users", user_id, "swing")


class LongTermMemory:
    def __init__(self, store=None):
        if store is None:
            from langgraph.store.memory import InMemoryStore

            store = InMemoryStore()
        self.store = store

    def add_finding(self, user_id: str, kind: str, issue_codes: list[str], metrics: dict) -> dict:
        record = {
            "at": datetime.datetime.now().isoformat(timespec="seconds"),
            "kind": kind,
            "issue_codes": list(issue_codes),
            "metrics": dict(metrics),
        }
        self.store.put(_ns(user_id), uuid.uuid4().hex, record)
        return record

    def findings(self, user_id: str, limit: int = config.MEMORY_RECENT) -> list[dict]:
        """최근 판정 기록(최신이 앞)."""
        items = self.store.search(_ns(user_id), limit=1000)
        records = sorted((i.value for i in items), key=lambda r: r["at"], reverse=True)
        return records[:limit]

    def move(self, src: str, dst: str) -> int:
        """src 의 판정 기록을 dst 로 옮깁니다(닉네임을 정하거나 바꿀 때). 옮긴 건수를 돌려줍니다."""
        if src == dst:
            return 0
        items = self.store.search(_ns(src), limit=1000)
        for item in items:
            self.store.put(_ns(dst), item.key, item.value)
            self.store.delete(_ns(src), item.key)
        return len(items)

    def clear(self, user_id: str) -> int:
        items = self.store.search(_ns(user_id), limit=1000)
        for item in items:
            self.store.delete(_ns(user_id), item.key)
        return len(items)


def describe(records: list[dict]) -> str:
    """판정 기록을 코칭 모델이 읽을 문장으로 바꿉니다(최신이 앞)."""
    if not records:
        return ""
    lines = [f"이 사용자의 이전 자세 판정 {len(records)}건 (최신순, 교육용 기준값 기준):"]
    for r in records:
        measured = ", ".join(f"{METRIC_KO.get(k, k)} {v}°" for k, v in r["metrics"].items())
        codes = ", ".join(CODE_KO.get(c, c) for c in r["issue_codes"]) or "문제 없음"
        lines.append(f"- {r['at'][:16].replace('T', ' ')} {KIND_KO.get(r['kind'], r['kind'])}: {codes} ({measured})")
    repeated = [c for c, n in Counter(c for r in records for c in r["issue_codes"]).items() if n >= 2]
    if repeated:
        lines.append("반복된 문제: " + ", ".join(CODE_KO.get(c, c) for c in repeated))
    return "\n".join(lines)


def build_store(mode: str):
    """offline 은 메모리 저장소, live 는 SQLite 파일 저장소."""
    if mode != "live":
        return LongTermMemory()
    import sqlite3

    from langgraph.store.sqlite import SqliteStore

    conn = sqlite3.connect(config.MEMORY_DB, check_same_thread=False, isolation_level=None)
    store = SqliteStore(conn)
    store.setup()
    return LongTermMemory(store)
