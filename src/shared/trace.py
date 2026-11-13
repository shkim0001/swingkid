"""공통 유틸 — 응답 trace[{step, input, output}] 에 단계를 덧붙입니다. 그래프 노드와 sub-agent 가 함께 씁니다."""

from __future__ import annotations

from typing import Any


def short(value: Any, limit: int = 160) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def step(state: dict, name: str, input_: Any, output: Any) -> list[dict]:
    """state 의 기존 trace 뒤에 단계 하나를 붙인 새 목록을 돌려줍니다(상태를 직접 바꾸지 않음)."""
    return [*state.get("trace", []), {"step": name, "input": short(input_), "output": short(output)}]
