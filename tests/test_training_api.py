"""훈련 API(T2): 로그인 필요, 남의 항목 403, 판정 → 자동 추천, 관리자 보기·지우기를 확인한다."""

import datetime

import pytest
from fastapi.testclient import TestClient

from src.api import app as app_module
from src.shared import config

ADMIN_PW = "admin-pass-1234"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GOLF_MODE", "offline")
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PW)
    app_module.get_agent.cache_clear()
    app_module.get_accounts.cache_clear()
    yield TestClient(app_module.app)
    app_module.get_agent.cache_clear()
    app_module.get_accounts.cache_clear()


def _login(client, username, password="swing-1234"):
    client.post("/auth/logout")
    r = client.post("/auth/signup", json={"username": username, "password": password})
    if r.status_code != 200:
        r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text


def test_guest_gets_401(client):
    assert client.get("/training").status_code == 401
    assert client.post("/training/drills", json={"title": "퍼팅", "unit": "개", "target": 50}).status_code == 401
    assert client.post("/training/drills/x/logs", json={"count": 1}).status_code == 401
    assert client.delete("/training/logs/x").status_code == 401


def test_custom_drill_log_and_summary(client):
    _login(client, "alice")
    drill = client.post("/training/drills", json={"title": "1m 퍼팅", "unit": "개", "target": 50}).json()
    log = client.post(f"/training/drills/{drill['id']}/logs", json={"count": 50, "memo": "왼손 리드"}).json()
    assert log["day"] == datetime.date.today().isoformat()
    view = client.get("/training").json()
    (item,) = view["drills"]
    assert item["today_count"] == 50 and item["week_days"] == 1 and view["streak"] == 1
    assert [l["id"] for l in view["logs"]] == [log["id"]]
    assert client.delete(f"/training/logs/{log['id']}").json()["deleted"] is True
    assert client.delete(f"/training/drills/{drill['id']}").json()["archived"] is True
    assert client.get("/training").json()["drills"] == []


def test_validation_errors_are_422(client):
    _login(client, "alice")
    assert client.post("/training/drills", json={"title": "퍼팅", "unit": "번", "target": 50}).status_code == 422
    drill = client.post("/training/drills", json={"title": "셋업", "unit": "회", "target": 10}).json()
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    assert client.post(f"/training/drills/{drill['id']}/logs", json={"count": 10, "day": tomorrow}).status_code == 422
    assert client.post(f"/training/drills/{drill['id']}/logs", json={"count": 0}).status_code == 422


def test_other_accounts_items_are_403_and_missing_404(client):
    _login(client, "alice")
    drill = client.post("/training/drills", json={"title": "셋업", "unit": "회", "target": 10}).json()
    log = client.post(f"/training/drills/{drill['id']}/logs", json={"count": 10}).json()
    _login(client, "bob")
    assert client.get("/training").json()["drills"] == []
    assert client.post(f"/training/drills/{drill['id']}/logs", json={"count": 10}).status_code == 403
    assert client.delete(f"/training/drills/{drill['id']}").status_code == 403
    assert client.delete(f"/training/logs/{log['id']}").status_code == 403
    assert client.delete("/training/drills/nope").status_code == 404
    assert client.delete("/training/logs/nope").status_code == 404


def test_pose_finding_proposes_plan_for_logged_in_user(client):
    _login(client, "alice")
    r = client.post("/demo/fixture", json={"question": "어드레스 봐주세요", "fixture": "photo_spine_bad"}).json()
    assert r["issue_codes"] == [config.SPINE_TILT]
    p = r["training_proposal"]
    assert [d["title"] for d in p["drills"]] == ["클럽을 엉덩이 옆에 대고 셋업 반복"] and p["weekdays"] == [0, 2, 4]
    view = client.get("/training").json()
    assert view["drills"] == [] and [x["id"] for x in view["proposals"]] == [p["id"]]   # 받기 전엔 항목을 넣지 않음
    # 같은 문제가 다시 나와도 기다리는 제안이 있으면 또 만들지 않음
    again = client.post("/demo/fixture", json={"question": "다시 봐주세요", "fixture": "photo_spine_bad"}).json()
    assert again["training_proposal"] is None
    # 받으면 항목과 루틴이 생김
    accepted = client.post(f"/training/proposals/{p['id']}/accept").json()
    assert accepted["status"] == "accepted" and len(accepted["routines"]) == 1
    (item,) = client.get("/training").json()["drills"]
    assert item["key"] == "spine_club_setup" and item["doc_id"] == "address.md" and item["metric"] == "spine_tilt"
    assert client.get("/training").json()["proposals"] == []
    assert client.post(f"/training/proposals/{p['id']}/accept").status_code == 422   # 두 번 받기 불가


def test_no_proposal_for_guest_normal_pose_or_text(client):
    r = client.post("/demo/fixture", json={"question": "어드레스 봐주세요", "fixture": "photo_spine_bad"}).json()
    assert r["training_proposal"] is None   # 게스트
    _login(client, "alice")
    assert client.post("/demo/fixture", json={"question": "봐주세요", "fixture": "photo_normal"}).json()["training_proposal"] is None
    assert client.post("/query", json={"question": "그립은 어떻게 잡아요?"}).json()["training_proposal"] is None
    assert client.get("/training").json()["proposals"] == []


def test_dismiss_proposal_and_other_account_403(client):
    _login(client, "alice")
    p = client.post("/demo/fixture", json={"question": "봐주세요", "fixture": "video_bent_arm"}).json()["training_proposal"]
    assert len(p["drills"]) == 2
    _login(client, "bob")
    assert client.post(f"/training/proposals/{p['id']}/accept").status_code == 403
    assert client.post(f"/training/proposals/{p['id']}/dismiss").status_code == 403
    assert client.post("/training/proposals/nope/accept").status_code == 404
    _login(client, "alice")
    assert client.post(f"/training/proposals/{p['id']}/dismiss").json()["dismissed"] is True
    assert client.get("/training").json()["proposals"] == []


def test_library_is_public_and_add_from_library(client):
    lib = client.get("/training/library").json()
    assert len(lib["items"]) == 25 and len(lib["categories"]) == 8 and lib["levels"]["1"] == "입문"
    assert client.post("/training/drills/from-library", json={"key": "putt_1m_ten"}).status_code == 401
    _login(client, "alice")
    d = client.post("/training/drills/from-library", json={"key": "putt_1m_ten"}).json()
    assert d["title"] == "1m 퍼팅 10번 연속 넣기" and d["source"] == "library"
    assert client.post("/training/drills/from-library", json={"key": "putt_1m_ten"}).json()["id"] == d["id"]
    assert client.post("/training/drills/from-library", json={"key": "nope"}).status_code == 422


def test_plans_routines_and_calendar(client):
    today = datetime.date.today()
    _login(client, "alice")
    d = client.post("/training/drills/from-library", json={"key": "putt_1m_ten"}).json()
    plan = client.post("/training/plans", json={"drill_id": d["id"], "day": today.isoformat()}).json()
    view = client.get("/training").json()
    assert [p["title"] for p in view["today_plan"]["planned"]] == ["1m 퍼팅 10번 연속 넣기"]
    assert view["today_plan"]["planned"][0]["status"] == "today"
    client.post(f"/training/drills/{d['id']}/logs", json={"count": 10})
    cal = client.get(f"/training/calendar?year={today.year}&month={today.month}").json()
    day = next(x for w in cal["weeks"] for x in w if x["date"] == today.isoformat())
    assert day["planned"][0]["status"] == "done" and cal["week_rate"]["rate"] == 1.0
    # 루틴: 매일, 오늘~내일. 오늘 하루 빼기
    r = client.post("/training/routines", json={"drill_id": d["id"], "weekdays": list(range(7)),
                                                 "start": today.isoformat(), "end": (today + datetime.timedelta(1)).isoformat()}).json()
    assert client.get("/training/calendar").json()["routines"][0]["id"] == r["id"]
    assert client.post(f"/training/routines/{r['id']}/skip", json={"day": today.isoformat()}).json()["skipped"] is True
    assert client.delete(f"/training/plans/{plan['id']}").json()["deleted"] is True
    assert client.get("/training").json()["today_plan"]["planned"] == []   # 계획 지움 + 루틴 오늘 뺌
    assert client.delete(f"/training/routines/{r['id']}").json()["deleted"] is True


def test_schedule_validation_and_ownership(client):
    today = datetime.date.today().isoformat()
    _login(client, "alice")
    d = client.post("/training/drills/from-library", json={"key": "putt_1m_ten"}).json()
    plan = client.post("/training/plans", json={"drill_id": d["id"], "day": today}).json()
    r = client.post("/training/routines", json={"drill_id": d["id"], "weekdays": [0], "start": today, "end": today}).json()
    bad = {"drill_id": d["id"], "weekdays": [], "start": today, "end": today}
    assert client.post("/training/routines", json=bad).status_code == 422
    assert client.get("/training/calendar?year=2026&month=13").status_code == 422
    _login(client, "bob")
    assert client.post("/training/plans", json={"drill_id": d["id"], "day": today}).status_code == 403
    assert client.delete(f"/training/plans/{plan['id']}").status_code == 403
    assert client.delete(f"/training/routines/{r['id']}").status_code == 403
    assert client.post(f"/training/routines/{r['id']}/skip", json={"day": today}).status_code == 403
    assert client.delete("/training/plans/nope").status_code == 404
    assert client.get("/training/calendar").json()["routines"] == []
    client.post("/auth/logout")
    assert client.get("/training/calendar").status_code == 401
    assert client.post("/training/plans", json={"drill_id": d["id"], "day": today}).status_code == 401


def test_admin_sees_and_removes_everyones_training(client):
    _login(client, "alice")
    drill = client.post("/training/drills", json={"title": "셋업", "unit": "회", "target": 10}).json()
    log = client.post(f"/training/drills/{drill['id']}/logs", json={"count": 10}).json()
    assert client.get("/admin/training").status_code == 403
    _login(client, "admin", ADMIN_PW)
    users = client.get("/admin/training").json()["users"]
    assert [u["username"] for u in users] == ["alice"]
    u = users[0]
    assert u["drills"] == 1 and u["week_days"] == 1 and u["streak"] == 1 and u["last_day"] == datetime.date.today().isoformat()
    assert u["week_rate"] == {"planned": 0, "done": 0, "rate": None}
    detail = client.get("/admin/training/alice").json()
    assert detail["drills"][0]["title"] == "셋업" and [l["id"] for l in detail["logs"]] == [log["id"]]
    assert detail["calendar"]["month"] == datetime.date.today().month
    assert client.get("/admin/training/nobody").status_code == 404
    assert client.delete(f"/admin/training/logs/{log['id']}").json()["deleted"] is True
    assert client.delete(f"/admin/training/drills/{drill['id']}").json()["archived"] is True
    assert client.get("/admin/training").json()["users"] == []


def test_admin_user_calendar_rates_and_routine_delete(client):
    today = datetime.date.today()
    _login(client, "alice")
    d = client.post("/training/drills/from-library", json={"key": "putt_1m_ten"}).json()
    r = client.post("/training/routines", json={"drill_id": d["id"], "weekdays": list(range(7)),
                                                 "start": today.isoformat(), "end": today.isoformat()}).json()
    client.post(f"/training/drills/{d['id']}/logs", json={"count": 10})
    assert client.get("/admin/training/alice").status_code == 403
    assert client.delete(f"/admin/training/routines/{r['id']}").status_code == 403
    _login(client, "admin", ADMIN_PW)
    (u,) = client.get("/admin/training").json()["users"]
    assert u["week_rate"]["rate"] == 1.0 and u["month_rate"]["rate"] == 1.0
    cal = client.get(f"/admin/training/alice?year={today.year}&month={today.month}").json()["calendar"]
    day = next(x for w in cal["weeks"] for x in w if x["date"] == today.isoformat())
    assert day["planned"][0]["status"] == "done" and cal["routines"][0]["id"] == r["id"]
    assert client.get("/admin/training/alice?month=13").status_code == 422
    assert client.delete(f"/admin/training/routines/{r['id']}").json()["deleted"] is True
    assert client.get("/admin/training/alice").json()["calendar"]["routines"] == []
    client.post("/auth/logout")
    assert client.get("/admin/training").status_code == 401
