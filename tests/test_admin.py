"""관리자: .env 의 ADMIN_PASSWORD 로만 생기고, 모든 계정의 코칭을 보고 지울 수 있지만 이어 쓸 수는 없는지 확인한다."""

import pytest
from fastapi.testclient import TestClient

from src.api import app as app_module
from src.accounts.auth import AccountStore, AuthError

ADMIN_PW = "admin-pass-1234"


def _thread(tid, q="그립은 어떻게 잡아요?"):
    return {"id": tid, "created": 1, "turns": [{"q": q, "ts": 1}]}


def test_admin_cannot_be_signed_up(tmp_path):
    store = AccountStore()
    for name in ("admin", "Admin", "ADMIN"):
        with pytest.raises(AuthError):
            store.signup(name, "swing-1234")


def test_ensure_admin_creates_and_syncs_password():
    store = AccountStore()
    store.ensure_admin("first-pass-1")
    assert store.user_for(store.login("admin", "first-pass-1")) == "admin"
    store.ensure_admin("second-pass-2")   # .env 가 바뀌면 비밀번호도 따라감
    with pytest.raises(AuthError):
        store.login("admin", "first-pass-1")
    store.login("admin", "second-pass-2")
    with pytest.raises(AuthError):
        store.ensure_admin("short")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GOLF_MODE", "offline")
    monkeypatch.setenv("ADMIN_PASSWORD", ADMIN_PW)
    app_module.get_agent.cache_clear()
    app_module.get_accounts.cache_clear()
    yield TestClient(app_module.app)
    app_module.get_agent.cache_clear()
    app_module.get_accounts.cache_clear()


def _login(client, username, password="swing-1234", signup=True):
    client.post("/auth/logout")
    if signup:
        r = client.post("/auth/signup", json={"username": username, "password": password})
        if r.status_code == 200:
            return r.json()
    r = client.post("/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def test_admin_sees_and_deletes_everyones_threads(client):
    _login(client, "alice"); client.put("/threads/web-a", json=_thread("web-a"))
    _login(client, "bob"); client.put("/threads/web-b", json=_thread("web-b"))
    me = _login(client, "admin", ADMIN_PW, signup=False)
    assert me["admin"] is True and client.get("/auth/me").json()["admin"] is True
    listed = client.get("/admin/threads").json()["threads"]
    assert sorted((t["owner"], t["thread"]["id"]) for t in listed) == [("alice", "web-a"), ("bob", "web-b")]
    assert client.get("/admin/avatars/alice.svg").headers["content-type"].startswith("image/svg+xml")
    assert client.get("/admin/avatars/nobody.svg").status_code == 404
    assert client.delete("/admin/threads/web-a").json()["deleted"] == 1
    _login(client, "alice", signup=False)
    assert client.get("/threads").json()["threads"] == []


def test_admin_cannot_continue_or_overwrite_others(client):
    _login(client, "alice"); client.post("/query", json={"question": "그립은 어떻게 잡아요?", "thread_id": "web-a"})
    _login(client, "admin", ADMIN_PW, signup=False)
    assert client.post("/query", json={"question": "이어서", "thread_id": "web-a"}).status_code == 403
    assert client.put("/threads/web-a", json=_thread("web-a")).status_code == 403


def test_non_admin_is_forbidden(client):
    assert client.get("/admin/threads").status_code == 401
    me = _login(client, "alice")
    assert me["admin"] is False and client.get("/auth/me").json()["admin"] is False
    assert client.get("/admin/threads").status_code == 403
    assert client.delete("/admin/threads/web-a").status_code == 403
    assert client.get("/admin/avatars/alice.svg").status_code == 403
    client.post("/auth/logout")
    assert client.post("/auth/signup", json={"username": "Admin", "password": "swing-1234"}).status_code == 422
