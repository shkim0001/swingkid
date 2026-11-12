"""훈련 API(T2): 로그인 필요, 남의 항목 403, 판정 → 자동 추천, 관리자 보기·지우기를 확인한다."""

import datetime

import pytest
from fastapi.testclient import TestClient

from src import app as app_module
from src import config

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


def test_pose_finding_recommends_drills_for_logged_in_user(client):
    _login(client, "alice")
    r = client.post("/demo/fixture", json={"question": "어드레스 봐주세요", "fixture": "photo_spine_bad"}).json()
    assert r["issue_codes"] == [config.SPINE_TILT]
    assert [d["title"] for d in r["training_added"]] == ["클럽을 엉덩이 옆에 대고 셋업 반복"]
    (item,) = client.get("/training").json()["drills"]
    assert item["source"] == "auto" and item["doc_id"] == "address.md" and item["metric"] == "spine_tilt"
    # 같은 문제가 다시 나와도 진행 중이면 또 넣지 않음
    again = client.post("/demo/fixture", json={"question": "다시 봐주세요", "fixture": "photo_spine_bad"}).json()
    assert again["training_added"] == []
    assert len(client.get("/training").json()["drills"]) == 1


def test_no_recommendation_for_guest_normal_pose_or_text(client):
    r = client.post("/demo/fixture", json={"question": "어드레스 봐주세요", "fixture": "photo_spine_bad"}).json()
    assert r["training_added"] == []   # 게스트
    _login(client, "alice")
    assert client.post("/demo/fixture", json={"question": "봐주세요", "fixture": "photo_normal"}).json()["training_added"] == []
    assert client.post("/query", json={"question": "그립은 어떻게 잡아요?"}).json()["training_added"] == []
    assert client.get("/training").json()["drills"] == []


def test_admin_sees_and_removes_everyones_training(client):
    _login(client, "alice")
    drill = client.post("/training/drills", json={"title": "셋업", "unit": "회", "target": 10}).json()
    log = client.post(f"/training/drills/{drill['id']}/logs", json={"count": 10}).json()
    assert client.get("/admin/training").status_code == 403
    _login(client, "admin", ADMIN_PW)
    users = client.get("/admin/training").json()["users"]
    assert [u["username"] for u in users] == ["alice"] and users[0]["drills"][0]["title"] == "셋업"
    assert client.delete(f"/admin/training/logs/{log['id']}").json()["deleted"] is True
    assert client.delete(f"/admin/training/drills/{drill['id']}").json()["archived"] is True
    assert client.get("/admin/training").json()["users"] == []
