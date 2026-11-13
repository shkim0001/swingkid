"""판정 기반 추천 플랜(T6): 문제 코드 → 2주 플랜 제안(월·수·금), 중복 없음, 받으면 루틴, 닫기."""

import datetime

import pytest

from src.shared import config
from src.training.service import PLAN_DAYS, PLAN_WEEKDAYS, TrainingError, TrainingStore

D = datetime.date
TODAY = D(2026, 11, 12)   # 목요일


@pytest.fixture
def store():
    return TrainingStore()


def test_proposal_content(store):
    p = store.propose("alice", [config.BENT_LEAD_ARM], today=TODAY)
    assert p["status"] == "pending" and p["issue_codes"] == [config.BENT_LEAD_ARM]
    assert [d["key"] for d in p["drills"]] == ["lead_arm_top_check", "lead_arm_towel_half"]
    assert p["weekdays"] == PLAN_WEEKDAYS == [0, 2, 4]
    assert p["start"] == "2026-11-12" and p["end"] == (TODAY + datetime.timedelta(days=PLAN_DAYS - 1)).isoformat()
    assert p["sessions"] == 6   # 11/12~11/25 의 월·수·금: 13, 16, 18, 20, 23, 25
    assert all(d["doc_id"] == "backswing.md" for d in p["drills"])


def test_multiple_codes_in_one_proposal(store):
    p = store.propose("alice", [config.SPINE_TILT, config.KNEE_FLEX, "guard_medical"], today=TODAY)
    assert p["issue_codes"] == [config.SPINE_TILT, config.KNEE_FLEX]
    assert [d["key"] for d in p["drills"]] == ["spine_club_setup", "knee_mirror_setup"]


def test_no_proposal_without_swing_codes(store):
    assert store.propose("alice", [], today=TODAY) is None
    assert store.propose("alice", [config.POSE_NOT_DETECTED, "guard_purchase"], today=TODAY) is None


def test_no_duplicate_pending_proposal(store):
    first = store.propose("alice", [config.SPINE_TILT], today=TODAY)
    assert store.propose("alice", [config.SPINE_TILT], today=TODAY) is None
    assert [p["id"] for p in store.proposals("alice")] == [first["id"]]
    # 다른 문제가 나오면 따로 제안
    assert store.propose("alice", [config.BENT_LEAD_ARM], today=TODAY) is not None


def test_accept_creates_drills_and_routines(store):
    p = store.propose("alice", [config.BENT_LEAD_ARM], today=TODAY)
    result = store.accept_proposal("alice", p["id"], today=TODAY)
    assert result["status"] == "accepted" and len(result["routines"]) == 2
    assert {d["key"] for d in store.drills("alice")} == {"lead_arm_top_check", "lead_arm_towel_half"}
    assert all(d["source"] == "auto" and d["issue_code"] == config.BENT_LEAD_ARM for d in store.drills("alice"))
    cal = store.calendar("alice", 2026, 11, today=TODAY)
    fri = next(d for w in cal["weeks"] for d in w if d["date"] == "2026-11-13")
    assert len(fri["planned"]) == 2 and all(x["status"] == "upcoming" for x in fri["planned"])
    assert store.proposals("alice") == []


def test_accept_reuses_existing_drill(store):
    existing = store.add_from_library("alice", "spine_club_setup")
    p = store.propose("alice", [config.SPINE_TILT], today=TODAY)
    store.accept_proposal("alice", p["id"], today=TODAY)
    assert [d["id"] for d in store.drills("alice")] == [existing["id"]]


def test_no_new_proposal_while_accepted_plan_is_running(store):
    p = store.propose("alice", [config.SPINE_TILT], today=TODAY)
    store.accept_proposal("alice", p["id"], today=TODAY)
    assert store.propose("alice", [config.SPINE_TILT], today=TODAY + datetime.timedelta(days=5)) is None
    # 플랜 기간이 끝난 뒤에는 다시 제안
    assert store.propose("alice", [config.SPINE_TILT], today=TODAY + datetime.timedelta(days=PLAN_DAYS)) is not None


def test_dismiss_and_cannot_accept_twice(store):
    p = store.propose("alice", [config.SPINE_TILT], today=TODAY)
    assert store.dismiss_proposal("bob", p["id"]) is False
    assert store.dismiss_proposal("alice", p["id"]) is True
    assert store.proposals("alice") == []
    with pytest.raises(TrainingError):
        store.accept_proposal("alice", p["id"], today=TODAY)
    # 닫은 뒤 같은 문제가 다시 나오면 새로 제안
    assert store.propose("alice", [config.SPINE_TILT], today=TODAY) is not None


def test_accept_late_starts_from_today(store):
    p = store.propose("alice", [config.SPINE_TILT], today=TODAY)
    later = TODAY + datetime.timedelta(days=3)
    (routine,) = store.accept_proposal("alice", p["id"], today=later)["routines"]
    assert routine["start"] == later.isoformat() and routine["end"] == p["end"]


def test_other_account_cannot_accept(store):
    p = store.propose("alice", [config.SPINE_TILT], today=TODAY)
    with pytest.raises(TrainingError):
        store.accept_proposal("bob", p["id"], today=TODAY)
    assert store.proposal_owner(p["id"]) == "alice"
