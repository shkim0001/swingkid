"""훈련 관리(T1): 자동 추천 규칙, 직접 추가 검증, 기록, 집계(날짜 경계), 계정 간 분리를 확인한다."""

import datetime

import pytest

from src import config
from src.training import (
    CATALOG,
    WEEKLY_TARGET_DAYS,
    TrainingError,
    TrainingStore,
    streak,
    week_bounds,
)

D = datetime.date
TODAY = D(2026, 11, 12)   # 목요일


@pytest.fixture
def store():
    return TrainingStore()


# ── 자동 추천 ──


def test_recommend_adds_catalog_drills_for_issue_codes(store):
    added = store.recommend("alice", [config.BENT_LEAD_ARM])
    keys = {d["key"] for d in added}
    expected = {c["key"] for c in CATALOG if c["issue_code"] == config.BENT_LEAD_ARM}
    assert keys == expected and len(expected) == 2
    assert all(d["source"] == "auto" and d["doc_id"] == "backswing.md" and d["metric"] == "lead_arm" for d in added)


def test_recommend_does_not_duplicate_active_drills(store):
    store.recommend("alice", [config.SPINE_TILT])
    assert store.recommend("alice", [config.SPINE_TILT, config.SPINE_TILT]) == []
    assert len(store.drills("alice")) == 1


def test_recommend_again_after_archive(store):
    (drill,) = store.recommend("alice", [config.SPINE_TILT])
    assert store.archive("alice", drill["id"]) is True
    assert len(store.recommend("alice", [config.SPINE_TILT])) == 1


def test_recommend_ignores_unknown_and_non_swing_codes(store):
    assert store.recommend("alice", [config.POSE_NOT_DETECTED, "guard_medical", "nope"]) == []


def test_every_catalog_drill_cites_an_existing_doc():
    for c in CATALOG:
        assert (config.PROJECT_ROOT / "data" / "docs" / c["doc_id"]).exists(), c


# ── 직접 추가 ──


def test_add_custom_drill(store):
    d = store.add_custom("alice", "1m 퍼팅", "개", 50)
    assert d["source"] == "custom" and d["issue_code"] is None and d["target"] == 50


@pytest.mark.parametrize(
    "title,unit,target",
    [("", "회", 10), ("x" * 41, "회", 10), ("퍼팅", "번", 10), ("퍼팅", "개", 0), ("퍼팅", "개", 1001)],
)
def test_add_custom_drill_validation(store, title, unit, target):
    with pytest.raises(TrainingError):
        store.add_custom("alice", title, unit, target)


# ── 기록 ──


def test_add_and_delete_log(store):
    d = store.add_custom("alice", "1m 퍼팅", "개", 50)
    log = store.add_log("alice", d["id"], TODAY, 50, "왼손 리드 의식", today=TODAY)
    assert log["count"] == 50 and log["memo"] == "왼손 리드 의식" and log["day"] == "2026-11-12"
    assert [l["id"] for l in store.logs("alice", d["id"])] == [log["id"]]
    assert store.delete_log("alice", log["id"]) is True
    assert store.logs("alice", d["id"]) == []


@pytest.mark.parametrize("day,count,memo", [(TODAY + datetime.timedelta(days=1), 10, ""), (TODAY, 0, ""), (TODAY, 10, "x" * 301)])
def test_log_validation(store, day, count, memo):
    d = store.add_custom("alice", "셋업", "회", 10)
    with pytest.raises(TrainingError):
        store.add_log("alice", d["id"], day, count, memo, today=TODAY)


def test_cannot_log_to_archived_drill(store):
    d = store.add_custom("alice", "셋업", "회", 10)
    store.archive("alice", d["id"])
    with pytest.raises(TrainingError):
        store.add_log("alice", d["id"], TODAY, 10, today=TODAY)


# ── 계정 간 분리 ──


def test_accounts_are_isolated(store):
    d = store.add_custom("alice", "셋업", "회", 10)
    log = store.add_log("alice", d["id"], TODAY, 10, today=TODAY)
    assert store.drills("bob") == []
    with pytest.raises(TrainingError):
        store.add_log("bob", d["id"], TODAY, 10, today=TODAY)
    assert store.archive("bob", d["id"]) is False
    assert store.delete_log("bob", log["id"]) is False
    assert len(store.logs("alice", d["id"])) == 1


# ── 집계 ──


def test_week_bounds_monday_to_sunday():
    assert week_bounds(TODAY) == (D(2026, 11, 9), D(2026, 11, 15))
    assert week_bounds(D(2026, 11, 9)) == (D(2026, 11, 9), D(2026, 11, 15))   # 월요일
    assert week_bounds(D(2026, 11, 15)) == (D(2026, 11, 9), D(2026, 11, 15))  # 일요일


def test_streak_counts_back_from_today_or_yesterday():
    assert streak(set(), TODAY) == 0
    assert streak({TODAY, TODAY - datetime.timedelta(1), TODAY - datetime.timedelta(2)}, TODAY) == 3
    # 오늘 아직 안 했어도 어제까지 이어졌으면 연속으로 봄
    assert streak({TODAY - datetime.timedelta(1), TODAY - datetime.timedelta(2)}, TODAY) == 2
    assert streak({TODAY - datetime.timedelta(2)}, TODAY) == 0


def test_summary_week_streak_and_totals(store):
    d = store.add_custom("alice", "셋업", "회", 10)
    for day, n in [(D(2026, 11, 8), 10), (D(2026, 11, 10), 10), (D(2026, 11, 11), 5), (D(2026, 11, 11), 5), (TODAY, 10)]:
        store.add_log("alice", d["id"], day, n, today=TODAY)
    s = store.summary("alice", today=TODAY)
    (item,) = s["drills"]
    assert item["week_days"] == 3            # 11/8(지난주 일요일)은 빠짐
    assert item["week_count"] == 30 and item["total_count"] == 40
    assert item["streak"] == 3 and item["today_count"] == 10
    assert s["week_days"] == 3 and s["week_target_days"] == WEEKLY_TARGET_DAYS and s["streak"] == 3
    assert s["week"] == {"start": "2026-11-09", "end": "2026-11-15"}


def test_summary_excludes_archived_drills(store):
    d = store.add_custom("alice", "셋업", "회", 10)
    store.add_log("alice", d["id"], TODAY, 10, today=TODAY)
    store.archive("alice", d["id"])
    assert store.summary("alice", today=TODAY)["drills"] == []


def test_admin_helpers(store):
    store.add_custom("alice", "셋업", "회", 10)
    store.add_custom("bob", "퍼팅", "개", 50)
    assert store.owners() == ["alice", "bob"]
    bob = store.drills("bob")[0]
    assert store.archive_any(bob["id"]) is True
    assert store.drills("bob") == []
