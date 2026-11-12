"""코칭 내역: 로그인 계정마다 자기 코칭만 보이고, 남의 대화방은 읽거나 이어 쓸 수 없는지 확인한다."""

import pytest
from fastapi.testclient import TestClient

from src import app as app_module
from src.history import HistoryStore, ThreadOwnedByOther


def _thread(tid, q="그립은 어떻게 잡아요?"):
    return {"id": tid, "created": 1, "turns": [{"q": q, "ts": 1}]}


# ── 저장소 ──


def test_store_lists_only_own_threads():
    h = HistoryStore()
    h.put("alice", "web-a", _thread("web-a"))
    h.put("bob", "web-b", _thread("web-b"))
    assert [t["id"] for t in h.list("alice")] == ["web-a"]
    assert [t["id"] for t in h.list("bob")] == ["web-b"]


def test_store_refuses_other_owner():
    h = HistoryStore()
    h.put("alice", "web-a", _thread("web-a"))
    with pytest.raises(ThreadOwnedByOther):
        h.put("bob", "web-a", _thread("web-a", "덮어쓰기"))
    with pytest.raises(ThreadOwnedByOther):
        h.claim("web-a", "bob")
    assert h.list("alice")[0]["turns"][0]["q"] == "그립은 어떻게 잡아요?"


def test_deleted_thread_keeps_owner():
    h = HistoryStore()
    h.put("alice", "web-a", _thread("web-a"))
    assert h.delete("alice", "web-a") == 1
    assert h.list("alice") == []
    with pytest.raises(ThreadOwnedByOther):
        h.claim("web-a", "bob")


def test_claimed_thread_without_content_is_not_listed():
    h = HistoryStore()
    h.claim("web-a", "alice")
    assert h.list("alice") == []


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
    if r.status_code == 409:
        r = client.post("/auth/login", json={"username": username, "password": "swing-1234"})
    assert r.status_code == 200


def test_each_account_sees_only_own_threads(client):
    _login(client, "alice")
    assert client.put("/threads/web-a", json=_thread("web-a")).status_code == 200
    _login(client, "bob")
    assert client.get("/threads").json()["threads"] == []
    assert client.put("/threads/web-b", json=_thread("web-b")).status_code == 200
    assert [t["id"] for t in client.get("/threads").json()["threads"]] == ["web-b"]
    _login(client, "alice")
    assert [t["id"] for t in client.get("/threads").json()["threads"]] == ["web-a"]


def test_guest_cannot_use_threads_api(client):
    assert client.get("/threads").status_code == 401
    assert client.put("/threads/web-a", json=_thread("web-a")).status_code == 401
    assert client.delete("/threads").status_code == 401


def test_cannot_overwrite_or_continue_other_accounts_thread(client):
    _login(client, "alice")
    assert client.post("/query", json={"question": "그립은 어떻게 잡아요?", "thread_id": "web-a"}).status_code == 200
    _login(client, "bob")
    assert client.put("/threads/web-a", json=_thread("web-a")).status_code == 403
    assert client.post("/query", json={"question": "아까 뭐라고 했죠?", "thread_id": "web-a"}).status_code == 403
    body = {"question": "어드레스 봐주세요", "fixture": "photo_spine_bad", "thread_id": "web-a"}
    assert client.post("/demo/fixture", json=body).status_code == 403
    client.post("/auth/logout")
    assert client.post("/query", json={"question": "아까 뭐라고 했죠?", "thread_id": "web-a"}).status_code == 403


def test_guest_threads_still_work(client):
    r = client.post("/query", json={"question": "그립은 어떻게 잡아요?", "thread_id": "web-guest"})
    assert r.status_code == 200
    assert client.post("/query", json={"question": "그립은 어떻게 잡아요?", "thread_id": "web-guest"}).status_code == 200


def test_delete_one_and_all(client):
    _login(client, "alice")
    client.put("/threads/web-a", json=_thread("web-a"))
    client.put("/threads/web-b", json=_thread("web-b"))
    assert client.delete("/threads/web-a").json()["deleted"] == 1
    assert [t["id"] for t in client.get("/threads").json()["threads"]] == ["web-b"]
    assert client.delete("/threads").json()["deleted"] == 1
    assert client.get("/threads").json()["threads"] == []


def test_bad_thread_body_is_rejected(client):
    _login(client, "alice")
    assert client.put("/threads/web-a", json=_thread("web-x")).status_code == 422
    assert client.put("/threads/web-a", json={"id": "web-a"}).status_code == 422
    assert client.put("/threads/bad id", json=_thread("bad id")).status_code == 422
    big = _thread("web-a", "x" * (2 * 1024 * 1024 + 10))
    assert client.put("/threads/web-a", json=big).status_code == 413
