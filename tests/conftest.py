"""miniPJT 테스트 공통 도우미. 모든 테스트는 실제 API(Bedrock·Titan·MediaPipe)를 호출하지 않습니다."""

import json

import pytest

from src import config
from src.agent import build_agent


def load_fixture(name: str) -> dict:
    return json.loads((config.FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def agent():
    """가짜 모델 + BM25 로 만든 오프라인 에이전트."""
    return build_agent("offline")
