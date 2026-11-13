"""훈련 라이브러리(T4): data/training_library.json 의 항목이 규칙에 맞고, 판정 추천·내 항목 추가가 동작하는지 확인한다."""

import pytest

from src.shared import config
from src.training.service import CATALOG, LEVELS, LIBRARY, LOCATIONS, UNITS, TrainingError, TrainingStore

ITEMS = LIBRARY["items"]
SWING_CODES = {config.SPINE_TILT, config.KNEE_FLEX, config.BENT_LEAD_ARM}


def test_library_size_and_categories():
    assert 20 <= len(ITEMS) <= 30
    assert len(LIBRARY["categories"]) == 8
    assert {i["category"] for i in ITEMS} == set(LIBRARY["categories"])   # 분야마다 항목이 있음


def test_keys_are_unique():
    keys = [i["key"] for i in ITEMS]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("item", ITEMS, ids=lambda i: i["key"])
def test_item_fields(item):
    assert item["category"] in LIBRARY["categories"]
    assert item["location"] in LOCATIONS
    assert item["unit"] in UNITS
    assert item["level"] in LEVELS
    assert isinstance(item["minutes"], int) and 1 <= item["minutes"] <= 120
    assert isinstance(item["target"], int) and 1 <= item["target"] <= 1000
    assert 1 <= len(item["title"]) <= 40 and len(item["how"]) >= 20
    assert isinstance(item["equipment"], list)
    assert (config.PROJECT_ROOT / "data" / "docs" / item["doc_id"]).exists()
    assert set(item["issue_codes"]) <= SWING_CODES
    assert item["metric"] in (None, "spine_tilt", "knee_flex", "lead_arm")
    if item["issue_codes"]:
        assert item["metric"] is not None   # 판정과 연결된 항목은 각도 그래프를 그릴 수 있어야 함


def test_every_swing_code_has_recommended_drills():
    for code in SWING_CODES:
        assert any(code in c["issue_codes"] for c in CATALOG), code


def test_add_from_library_and_no_duplicate():
    store = TrainingStore()
    d = store.add_from_library("alice", "putt_1m_ten")
    assert d["source"] == "library" and d["title"] == "1m 퍼팅 10번 연속 넣기" and d["doc_id"] == "putting.md"
    assert store.add_from_library("alice", "putt_1m_ten")["id"] == d["id"]
    assert len(store.drills("alice")) == 1
    with pytest.raises(TrainingError):
        store.add_from_library("alice", "nope")


def test_recommend_reuses_library_drill_already_added():
    store = TrainingStore()
    store.add_from_library("alice", "spine_club_setup")
    assert store.recommend("alice", [config.SPINE_TILT]) == []   # 이미 내 항목에 있으면 다시 넣지 않음
