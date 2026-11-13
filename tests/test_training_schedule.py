"""훈련 일정(T5): 날짜 배치, 요일 루틴(기간 경계), 루틴 하루 빼기, 월 달력, 완료 판정, 달성률(분모는 오늘까지)."""

import datetime

import pytest

from src.training.service import TrainingError, TrainingStore, month_grid

D = datetime.date
TODAY = D(2026, 11, 12)   # 목요일
MON, TUE, WED, THU, FRI, SAT, SUN = range(7)


@pytest.fixture
def store():
    return TrainingStore()


@pytest.fixture
def drill(store):
    return store.add_custom("alice", "1m 퍼팅", "개", 10)


def _day(cal, day):
    return next(d for week in cal["weeks"] for d in week if d["date"] == day.isoformat())


# ── 달력 격자 ──


def test_month_grid_starts_monday_and_covers_month():
    weeks = month_grid(2026, 11)
    assert weeks[0][0] == D(2026, 10, 26) and weeks[0][0].weekday() == MON
    assert weeks[-1][-1] == D(2026, 12, 6) and weeks[-1][-1].weekday() == SUN
    days = [d for w in weeks for d in w]
    assert D(2026, 11, 1) in days and D(2026, 11, 30) in days and all(len(w) == 7 for w in weeks)


# ── 날짜 배치 ──


def test_add_plan_and_no_duplicate_same_day(store, drill):
    p1 = store.add_plan("alice", drill["id"], TODAY)
    p2 = store.add_plan("alice", drill["id"], TODAY)
    assert p1["id"] == p2["id"]
    assert len(_day(store.calendar("alice", 2026, 11, today=TODAY), TODAY)["planned"]) == 1


def test_plan_needs_own_active_drill(store, drill):
    with pytest.raises(TrainingError):
        store.add_plan("bob", drill["id"], TODAY)
    store.archive("alice", drill["id"])
    with pytest.raises(TrainingError):
        store.add_plan("alice", drill["id"], TODAY)


def test_delete_plan_only_own(store, drill):
    p = store.add_plan("alice", drill["id"], TODAY)
    assert store.delete_plan("bob", p["id"]) is False
    assert store.delete_plan("alice", p["id"]) is True
    assert _day(store.calendar("alice", 2026, 11, today=TODAY), TODAY)["planned"] == []


# ── 요일 루틴 ──


def test_routine_expands_weekdays_within_period(store, drill):
    store.add_routine("alice", drill["id"], [MON, WED, FRI], D(2026, 11, 4), D(2026, 11, 13))
    cal = store.calendar("alice", 2026, 11, today=TODAY)
    planned_days = sorted(d["date"] for w in cal["weeks"] for d in w if d["planned"])
    # 11/4(수) 시작, 11/13(금) 끝 — 시작·끝 날짜 모두 포함, 범위 밖 월·수·금은 없음
    assert planned_days == ["2026-11-04", "2026-11-06", "2026-11-09", "2026-11-11", "2026-11-13"]


@pytest.mark.parametrize(
    "weekdays,start,end",
    [([], TODAY, TODAY), ([7], TODAY, TODAY), ([MON], TODAY, TODAY - datetime.timedelta(1)),
     ([MON], TODAY, TODAY + datetime.timedelta(400))],
)
def test_routine_validation(store, drill, weekdays, start, end):
    with pytest.raises(TrainingError):
        store.add_routine("alice", drill["id"], weekdays, start, end)


def test_routine_and_plan_same_day_counted_once(store, drill):
    store.add_routine("alice", drill["id"], [THU], TODAY, TODAY)
    store.add_plan("alice", drill["id"], TODAY)
    (item,) = _day(store.calendar("alice", 2026, 11, today=TODAY), TODAY)["planned"]
    assert item["plan_id"] and item["routine_id"]


def test_skip_one_routine_day(store, drill):
    r = store.add_routine("alice", drill["id"], [MON, WED, FRI], D(2026, 11, 2), D(2026, 11, 30))
    assert store.skip_routine_day("alice", r["id"], D(2026, 11, 6)) is True
    assert store.skip_routine_day("bob", r["id"], D(2026, 11, 9)) is False
    cal = store.calendar("alice", 2026, 11, today=TODAY)
    assert _day(cal, D(2026, 11, 6))["planned"] == [] and _day(cal, D(2026, 11, 9))["planned"]


def test_delete_routine(store, drill):
    r = store.add_routine("alice", drill["id"], [THU], D(2026, 11, 1), D(2026, 11, 30))
    assert store.delete_routine("bob", r["id"]) is False
    assert store.delete_routine("alice", r["id"]) is True
    assert all(not d["planned"] for w in store.calendar("alice", 2026, 11, today=TODAY)["weeks"] for d in w)


# ── 완료 판정 ──


def test_status_done_today_missed_upcoming(store, drill):
    store.add_routine("alice", drill["id"], [MON, WED, THU, FRI], D(2026, 11, 9), D(2026, 11, 13))
    store.add_log("alice", drill["id"], D(2026, 11, 9), 10, today=TODAY)   # 월 완료
    cal = store.calendar("alice", 2026, 11, today=TODAY)
    status = lambda day: _day(cal, day)["planned"][0]["status"]
    assert status(D(2026, 11, 9)) == "done"
    assert status(D(2026, 11, 11)) == "missed"     # 수: 지났는데 기록 없음
    assert status(TODAY) == "today"                # 목: 오늘, 아직
    assert status(D(2026, 11, 13)) == "upcoming"   # 금: 예정
    assert _day(cal, D(2026, 11, 9))["planned"][0]["count"] == 10


def test_unplanned_practice_is_shown_as_extra(store, drill):
    other = store.add_custom("alice", "셋업", "회", 10)
    store.add_plan("alice", drill["id"], TODAY)
    store.add_log("alice", other["id"], TODAY, 10, today=TODAY)
    day = _day(store.calendar("alice", 2026, 11, today=TODAY), TODAY)
    assert [p["drill_id"] for p in day["planned"]] == [drill["id"]]
    assert [e["drill_id"] for e in day["extra"]] == [other["id"]] and day["extra"][0]["count"] == 10


# ── 달성률 ──


def test_rates_count_only_up_to_today(store, drill):
    store.add_routine("alice", drill["id"], [MON, WED, THU, FRI], D(2026, 11, 9), D(2026, 11, 30))
    store.add_log("alice", drill["id"], D(2026, 11, 9), 10, today=TODAY)
    store.add_log("alice", drill["id"], TODAY, 10, today=TODAY)
    cal = store.calendar("alice", 2026, 11, today=TODAY)
    # 이번 주(11/9~15) 오늘까지 계획: 월·수·목 = 3, 완료: 월·목 = 2 (금은 아직 오지 않아 분모에서 빠짐)
    assert cal["week_rate"] == {"planned": 3, "done": 2, "rate": round(2 / 3, 3)}
    # 이번 달 오늘까지도 같음(루틴이 11/9 시작)
    assert cal["month_rate"] == {"planned": 3, "done": 2, "rate": round(2 / 3, 3)}


def test_rate_is_none_without_plans(store, drill):
    cal = store.calendar("alice", 2026, 11, today=TODAY)
    assert cal["week_rate"] == {"planned": 0, "done": 0, "rate": None}


def test_calendar_is_per_account(store, drill):
    store.add_plan("alice", drill["id"], TODAY)
    assert all(not d["planned"] for w in store.calendar("bob", 2026, 11, today=TODAY)["weeks"] for d in w)


def test_archived_drill_disappears_from_calendar(store, drill):
    store.add_plan("alice", drill["id"], TODAY)
    store.archive("alice", drill["id"])
    assert _day(store.calendar("alice", 2026, 11, today=TODAY), TODAY)["planned"] == []


def test_today_plan_list(store, drill):
    store.add_plan("alice", drill["id"], TODAY)
    today = store.day_plan("alice", TODAY, today=TODAY)
    assert today["date"] == "2026-11-12" and [p["title"] for p in today["planned"]] == ["1m 퍼팅"]
