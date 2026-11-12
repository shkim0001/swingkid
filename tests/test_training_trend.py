"""각도 변화 그래프(T10): 판정 기록에서 항목의 각도 추이를 코드로 계산하고, API 로 본인 것만 돌려주는지 확인한다."""

import pytest
from fastapi.testclient import TestClient

from src import app as app_module
from src.training import angle_trend


def _rec(at, **metrics):
    return {"at": at, "kind": "photo", "issue_codes": [], "metrics": metrics}


def test_trend_sorted_with_range_and_change():
    records = [   # 장기 기억은 최신이 앞
        _rec("2026-11-12T10:00:00", spine_tilt=38.0, knee_flex=150.0),
        _rec("2026-11-05T10:00:00", spine_tilt=50.2),
        _rec("2026-11-08T10:00:00", knee_flex=170.0),
        _rec("2026-11-01T10:00:00", spine_tilt=52.0),
    ]
    t = angle_trend(records, "spine_tilt")
    assert t["label"] == "척추 앞기울기" and t["range"] == {"lo": 30.0, "hi": 45.0}
    assert [p["value"] for p in t["points"]] == [52.0, 50.2, 38.0]   # 오래된 것부터, 그 각도가 있는 기록만
    assert [p["in_range"] for p in t["points"]] == [False, False, True]
    assert t["first"] == 52.0 and t["last"] == 38.0 and t["change"] == -14.0
    assert t["in_range_count"] == 1 and t["latest_in_range"] is True


def test_lead_arm_has_only_lower_bound():
    t = angle_trend([_rec("2026-11-01T10:00:00", lead_arm=150.0), _rec("2026-11-02T10:00:00", lead_arm=165.0)], "lead_arm")
    assert t["range"] == {"lo": 160.0, "hi": None}
    assert [p["in_range"] for p in t["points"]] == [False, True]


def test_empty_and_unknown_metric():
    t = angle_trend([], "knee_flex")
    assert t["points"] == [] and t["first"] is None and t["change"] is None and t["latest_in_range"] is None
    with pytest.raises(ValueError):
        angle_trend([], "nope")


# ── API ──


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GOLF_MODE", "offline")
    app_module.get_agent.cache_clear()
    app_module.get_accounts.cache_clear()
    yield TestClient(app_module.app)
    app_module.get_agent.cache_clear()
    app_module.get_accounts.cache_clear()


def _login(client, username):
    client.post("/auth/logout")
    r = client.post("/auth/signup", json={"username": username, "password": "swing-1234"})
    if r.status_code != 200:
        r = client.post("/auth/login", json={"username": username, "password": "swing-1234"})
    assert r.status_code == 200


def test_trend_api_uses_my_judgments(client):
    _login(client, "alice")
    client.post("/demo/fixture", json={"question": "어드레스 봐주세요", "fixture": "photo_spine_bad"})
    client.post("/demo/fixture", json={"question": "다시 찍었어요", "fixture": "photo_normal"})
    d = client.post("/training/drills/from-library", json={"key": "spine_club_setup"}).json()
    t = client.get(f"/training/drills/{d['id']}/trend").json()
    assert t["metric"] == "spine_tilt" and len(t["points"]) == 2
    assert t["points"][0]["in_range"] is False and t["points"][1]["in_range"] is True
    # 각도와 연결되지 않은 항목은 422, 남의 항목 403, 게스트 401
    putt = client.post("/training/drills/from-library", json={"key": "putt_1m_ten"}).json()
    assert client.get(f"/training/drills/{putt['id']}/trend").status_code == 422
    _login(client, "bob")
    assert client.get(f"/training/drills/{d['id']}/trend").status_code == 403
    client.post("/auth/logout")
    assert client.get(f"/training/drills/{d['id']}/trend").status_code == 401
