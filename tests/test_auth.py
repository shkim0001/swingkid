"""계정: 가입·로그인·로그아웃과 세션 쿠키를 확인한다."""

import pytest
from fastapi.testclient import TestClient

from src.api import app as app_module
from src.accounts.auth import AccountStore, AuthError, memory_owner


@pytest.fixture
def store():
    return AccountStore()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GOLF_MODE", "offline")
    app_module.get_agent.cache_clear()
    app_module.get_accounts.cache_clear()
    yield TestClient(app_module.app)
    app_module.get_agent.cache_clear()
    app_module.get_accounts.cache_clear()


# ── 저장소 (순수 파이썬) ──


def test_signup_login_logout(store):
    store.signup("골프키드", "swing-1234")
    token = store.login("골프키드", "swing-1234")
    assert store.user_for(token) == "골프키드"
    store.logout(token)
    assert store.user_for(token) is None


def test_password_is_not_stored_in_plain_text(store):
    store.signup("alice", "swing-1234")
    rows = store._conn.execute("SELECT * FROM users").fetchall()
    assert all(b"swing-1234" not in (v if isinstance(v, bytes) else str(v).encode()) for row in rows for v in row)


def test_session_token_is_stored_as_hash(store):
    store.signup("alice", "swing-1234")
    token = store.login("alice", "swing-1234")
    assert store._conn.execute("SELECT COUNT(*) FROM sessions WHERE token_sha = ?", (token,)).fetchone()[0] == 0


@pytest.mark.parametrize("username", ["a", "띄어 쓰기", "x" * 33, "semi;colon", ""])
def test_bad_username_is_rejected(store, username):
    with pytest.raises(AuthError):
        store.signup(username, "swing-1234")


@pytest.mark.parametrize("password", ["short", "x" * 129])
def test_bad_password_is_rejected(store, password):
    with pytest.raises(AuthError):
        store.signup("alice", password)


def test_duplicate_username_is_rejected(store):
    store.signup("alice", "swing-1234")
    with pytest.raises(AuthError, match="이미"):
        store.signup("alice", "other-pass-1")


def test_wrong_password_and_unknown_user_get_same_message(store):
    store.signup("alice", "swing-1234")
    with pytest.raises(AuthError) as wrong:
        store.login("alice", "wrong-pass")
    with pytest.raises(AuthError) as unknown:
        store.login("nobody", "swing-1234")
    assert str(wrong.value) == str(unknown.value)


def test_unknown_or_expired_token(store):
    assert store.user_for(None) is None
    assert store.user_for("not-a-token") is None
    store.signup("alice", "swing-1234")
    token = store.login("alice", "swing-1234")
    store._conn.execute("UPDATE sessions SET expires_at = '2000-01-01T00:00:00+00:00'")
    assert store.user_for(token) is None


def test_memory_owner_does_not_collide_with_old_nicknames():
    assert memory_owner("골프키드") != "골프키드"


# ── API ──


def test_api_signup_sets_httponly_cookie_and_me(client):
    assert client.get("/auth/me").json()["username"] == None
    r = client.post("/auth/signup", json={"username": "골프키드", "password": "swing-1234"})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert client.get("/auth/me").json()["username"] == "골프키드"


def test_api_logout_then_login(client):
    client.post("/auth/signup", json={"username": "alice", "password": "swing-1234"})
    client.post("/auth/logout")
    assert client.get("/auth/me").json()["username"] == None
    assert client.post("/auth/login", json={"username": "alice", "password": "nope-nope"}).status_code == 401
    assert client.post("/auth/login", json={"username": "alice", "password": "swing-1234"}).status_code == 200
    assert client.get("/auth/me").json()["username"] == "alice"


def test_api_signup_errors(client):
    client.post("/auth/signup", json={"username": "alice", "password": "swing-1234"})
    client.post("/auth/logout")
    assert client.post("/auth/signup", json={"username": "alice", "password": "swing-5678"}).status_code == 409
    assert client.post("/auth/signup", json={"username": "a b", "password": "swing-1234"}).status_code == 422
    assert client.post("/auth/signup", json={"username": "carol", "password": "short"}).status_code == 422


def test_cookie_is_secure_behind_https_proxy(client):
    r = client.post(
        "/auth/signup", json={"username": "alice", "password": "swing-1234"}, headers={"X-Forwarded-Proto": "https"}
    )
    assert "secure" in r.headers["set-cookie"].lower()
