"""coach 도메인 프롬프트 — 코칭 시스템 프롬프트와, 상태(판정·근거·기억)로 사용자 프롬프트를 만드는 함수.

프롬프트 문장은 여기서만 고칩니다. 그래프(graph.py)는 build_prompt() 결과를 모델에 넘기기만 합니다.
"""

from __future__ import annotations

import datetime

from src.memory.store import describe
from src.shared import config

HISTORY_TURNS = 6  # 코칭 프롬프트에 넣을 최근 대화 수(질문+답변 쌍 기준 3턴)

SYSTEM_PROMPT = """당신은 골프를 시작한 지 0~3개월 된 입문자를 돕는 골프 코치입니다.
규칙:
1. 정중한 존댓말로, 입문자가 이해할 수 있는 쉬운 말로 답합니다.
2. 반드시 아래 '근거 문서' 안의 내용만 사용합니다. 문서에 없는 내용은 지어내지 않습니다.
3. '판정'이 있으면 측정 각도와 교육용 기준값을 먼저 알려 주고, 근거 문서의 교정 방법과 연습 방법을 안내합니다.
4. 장비 구매 추천, 가격, 의학적 진단은 하지 않습니다.
   이 질문은 시스템이 이미 답해도 되는 범위로 판정했으므로, 근거 문서가 있으면 범위를 이유로 거절하지 않습니다.
   제품명·브랜드명은 쓰지 않고, 근거에 있는 일반 특징(예: SPF, 워터프루프)만으로 답합니다.
   근거에 없는 조건별 조언(피부 타입·체형별 등)을 덧붙이지 않습니다.
5. 근거가 '웹 검색 결과'이면 조회 날짜 기준의 정보라고 밝히고, 결과끼리 말이 다르면 단정하지 말고 그렇다고 알립니다.
   근거가 골프 규칙 문서이면 몇 년판 규칙 기준인지(예: 2023년판 골프 규칙 기준) 답변에 밝힙니다.
6. '이전 기록'(이 사용자의 지난 자세 판정)이 있으면 이번 판정과 비교해 좋아진 점이나 반복되는 문제를 한 문장으로 짚습니다.
   기록에 없는 날짜·수치는 만들지 않습니다.
7. '연습 기록'(이 사용자의 훈련 기록, 코드가 집계)이 있으면 그 수치만 사용해 연습 상황을 짚고, 기록에 없는 연습·날짜는 만들지 않습니다.
8. 답변은 5문장 이내로 짧게 씁니다."""

METRIC_LABELS = {
    "spine_tilt": ("척추 앞기울기", config.SPINE_TILT_RANGE),
    "knee_flex": ("무릎 각도", config.KNEE_FLEX_RANGE),
    "lead_arm": ("백스윙 정점 왼팔 각도", (config.LEAD_ARM_MIN, None)),   # 상한 없음: 문서 표현 그대로 '160° 이상'
}

MEDIA_LABELS = {"photo": "측면 어드레스 사진", "video": "정면 스윙 영상"}


def _standard(lo: float, hi: float | None) -> str:
    """교육용 기준값을 근거 문서와 같은 표현으로 (예: 30~45°, 160° 이상)."""
    return f"{lo:g}° 이상" if hi is None else f"{lo:g}~{hi:g}°"


def _is_web(doc_id: str) -> bool:
    return doc_id.startswith(("http://", "https://"))


def _last_finding_line(state: dict) -> str:
    """'직전 판정(같은 대화)' 한 줄: 종류 · 문제 코드 · 측정값 · 측정한 항목."""
    kind = state["last_media_kind"]
    codes = state.get("last_issue_codes") or ["문제 없음"]
    measured, labels = [], []
    for key, value in state["last_metrics"].items():
        label, (lo, hi) = METRIC_LABELS[key]
        labels.append(label)
        measured.append(f"{label} {value}° (교육용 기준 {_standard(lo, hi)})")
    return (
        f"직전 판정(같은 대화, {MEDIA_LABELS.get(kind, kind)}): {', '.join(codes)} | " + ", ".join(measured)
        + f"\n측정한 항목은 {', '.join(labels)}뿐입니다. 그 밖의 부위는 측정값이 없으니 수치를 만들지 말고 근거 문서의 설명과 점검 방법으로 안내합니다."
    )


def continues_finding(state: dict) -> bool:
    """직전 판정이 있고, 이번 근거에 그 판정의 근거 문서가 붙었으면(=판정에 이어지는 질문) True."""
    kind = state.get("last_media_kind")
    if not kind or not state.get("last_metrics"):
        return False
    return any(c["doc_id"] == config.MEDIA_SOURCE[kind] for c in state.get("contexts", []))


def build_prompt(state: dict) -> str:
    """이번 질문·판정·이전 기록·연습 기록·근거 문서를 모아 coach 에게 줄 사용자 프롬프트를 만듭니다."""
    lines = [f"질문: {state['question']}"]
    finding = state.get("finding")
    if finding:
        codes = state.get("issue_codes") or ["문제 없음"]
        measured = []
        for key, value in finding["metrics"].items():
            label, (lo, hi) = METRIC_LABELS[key]
            measured.append(f"{label} {value}° (교육용 기준 {_standard(lo, hi)})")
        lines.append(f"판정: {', '.join(codes)} | " + ", ".join(measured))
        if state.get("memory"):   # 새 판정이면 이전 기록과 비교할 수 있게 함께 줍니다
            lines.append("이전 기록: " + describe(state["memory"]))
        if state.get("practice"):   # 새 판정이면 그동안의 연습도 함께 짚을 수 있게
            lines.append("연습 기록: " + state["practice"].replace("\n", " / "))
    elif continues_finding(state):
        # 같은 대화의 직전 판정에 이어지는 질문: 판정 내용과 '무엇을 쟀는지'를 다시 알려 줍니다.
        lines.append(_last_finding_line(state))
    lines.append("근거 문서: " + ", ".join(dict.fromkeys(c["doc_id"] for c in state["contexts"])))
    if any(_is_web(c["doc_id"]) for c in state["contexts"]):
        lines.append(f"(근거가 웹 검색 결과입니다. 조회 날짜: {datetime.date.today().isoformat()})")
    for c in state["contexts"]:
        lines.append(f"\n[{c['doc_id']}]\n{c['text']}")
    return "\n".join(lines)
