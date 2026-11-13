"""Google 계정 로그인: 아이디 만들기, 계정 연결, ID 토큰 검사, 로그인·콜백 경로 (실제 Google 호출 없음)."""

import base64
import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from src.accounts.auth import AccountStore, AuthError, google_username_base
from src.accounts.google import TOKEN_ENDPOINT, GoogleOAuth, parse_id_token
from src.api import app as app_module

CLIENT_ID = "test-client.apps.googleusercontent.com"


def id_token(**claims) -> str:
    """서명 없는 테스트용 ID 토큰 (서버는 토큰 주소에서 직접 받은 것만 쓰므로 서명을 보지 않음)."""
    body = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "exp": time.time() + 3600,
            "sub": "1001", "email": "kim.golf@gmail.com", "nonce": "n-1", **claims}
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
    return f"{enc({'alg': 'RS256'})}.{enc(body)}.sig"


# ── 아이디 만들기 · 계정 연결 (순수 파이썬) ──


@pytest.mark.parametrize("email, expected", [
    ("kim.golf+1@gmail.com", "kim_golf_1"),
    ("swingkid@gmail.com", "swingkid"),
    ("a@gmail.com", "golfer"),
    ("..__@gmail.com", "golfer"),
    ("x" * 40 + "@gmail.com", "x" * 28),
])
def test_username_from_email_front_part(email, expected):
    assert google_username_base(email) == expected


def test_google_login_creates_account_once_and_reuses_it_by_sub():
    store = AccountStore()
    token, name, created = store.google_login("1001", "kim.golf@gmail.com")
    assert (name, created) == ("kim_golf", True) and store.user_for(token) == "kim_golf"
    token2, name2, created2 = store.google_login("1001", "renamed@gmail.com")   # 이메일이 바뀌어도 같은 계정
    assert (name2, created2) == ("kim_golf", False) and store.user_for(token2) == "kim_golf"
    assert store.is_google("kim_golf") and store.avatar("kim_golf") is not None


def test_taken_names_get_number_suffix_and_admin_is_skipped():
    store = AccountStore()
    store.signup("swingkid", "swing-1234")
    assert store.google_login("1", "swingkid@gmail.com")[1] == "swingkid2"
    assert store.google_login("2", "swingkid@naver.com")[1] == "swingkid3"
    assert store.google_login("3", "admin@gmail.com")[1] == "admin2"
    assert store.google_login("4", "Admin@gmail.com")[1] == "Admin2"


def test_google_account_cannot_log_in_with_password():
    store = AccountStore()
    _, name, _ = store.google_login("1001", "kim.golf@gmail.com")
    for password in ("", "anything-123", "swing-1234"):
        with pytest.raises(AuthError):
            store.login(name, password)


def test_password_signup_still_works_next_to_google():
    store = AccountStore()
    store.google_login("1001", "kim.golf@gmail.com")
    store.signup("골프키드", "swing-1234")
    assert store.user_for(store.login("골프키드", "swing-1234")) == "골프키드"
    assert not store.is_google("골프키드")


# ── ID 토큰 검사 ──


def test_id_token_ok():
    identity = parse_id_token(id_token(), CLIENT_ID, "n-1")
    assert (identity.sub, identity.email) == ("1001", "kim.golf@gmail.com")


@pytest.mark.parametrize("claims, nonce", [
    ({"iss": "https://evil.example"}, "n-1"),
    ({"aud": "other-client"}, "n-1"),
    ({"exp": time.time() - 3600}, "n-1"),
    ({}, "other-nonce"),
    ({}, ""),
    ({"sub": ""}, "n-1"),
])
def test_id_token_rejected(claims, nonce):
    with pytest.raises(AuthError):
        parse_id_token(id_token(**claims), CLIENT_ID, nonce)


def test_garbage_id_token_rejected():
    with pytest.raises(AuthError):
        parse_id_token("not-a-jwt", CLIENT_ID, "n-1")


def test_exchange_posts_code_and_secret_to_token_endpoint():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["form"] = parse_qs(request.content.decode())
        return httpx.Response(200, json={"id_token": id_token()})

    google = GoogleOAuth(CLIENT_ID, "secret", transport=httpx.MockTransport(handler))
    identity = google.exchange("code-1", "http://testserver/auth/google/callback", "n-1")
    assert identity.sub == "1001" and seen["url"] == TOKEN_ENDPOINT
    assert seen["form"]["client_secret"] == ["secret"] and seen["form"]["code"] == ["code-1"]


def test_exchange_error_becomes_auth_error():
    google = GoogleOAuth(CLIENT_ID, "secret", transport=httpx.MockTransport(lambda r: httpx.Response(400, json={})))
    with pytest.raises(AuthError):
        google.exchange("bad", "http://testserver/auth/google/callback", "n-1")


# ── API: /auth/providers · /auth/google/login · /auth/google/callback ──


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GOLF_MODE", "offline")
    app_module.get_agent.cache_clear()
    app_module.get_accounts.cache_clear()
    yield TestClient(app_module.app, follow_redirects=False)
    app_module.get_agent.cache_clear()
    app_module.get_accounts.cache_clear()


def fake_google(monkeypatch, sub="1001", email="kim.golf@gmail.com"):
    """Google 토큰 주소를 흉내 냅니다. 로그인 요청 때 받은 nonce 를 그대로 ID 토큰에 넣어 줍니다."""
    state = {}

    def handler(request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        state["redirect_uri"] = form["redirect_uri"][0]
        return httpx.Response(200, json={"id_token": id_token(sub=sub, email=email, nonce=state["nonce"])})

    google = GoogleOAuth(CLIENT_ID, "secret", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(app_module, "get_google", lambda: google)
    return state


def start_login(client, state):
    r = client.get("/auth/google/login")
    assert r.status_code == 302
    query = parse_qs(urlparse(r.headers["location"]).query)
    state["nonce"] = query["nonce"][0]
    return query


def test_providers_hide_google_without_env(client, monkeypatch):
    monkeypatch.setattr(app_module, "get_google", lambda: None)
    assert client.get("/auth/providers").json() == {"password": True, "google": False}
    assert client.get("/auth/google/login").status_code == 404


def test_from_env_needs_both_values(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT_ID)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    assert GoogleOAuth.from_env() is None
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    assert GoogleOAuth.from_env().client_id == CLIENT_ID


def test_login_redirects_to_google_with_state_and_callback(client, monkeypatch):
    state = fake_google(monkeypatch)
    assert client.get("/auth/providers").json()["google"] is True
    query = start_login(client, state)
    assert query["client_id"] == [CLIENT_ID] and query["scope"] == ["openid email"]
    assert query["redirect_uri"] == ["http://testserver/auth/google/callback"]
    assert query["state"][0] and query["nonce"][0]


def test_callback_creates_account_and_logs_in(client, monkeypatch):
    state = fake_google(monkeypatch)
    query = start_login(client, state)
    r = client.get("/auth/google/callback", params={"code": "c-1", "state": query["state"][0]})
    assert r.status_code == 302 and r.headers["location"] == "/"
    assert state["redirect_uri"] == "http://testserver/auth/google/callback"
    assert client.get("/auth/me").json() == {"username": "kim_golf", "admin": False}
    # 같은 Google 계정으로 다시 로그인하면 같은 아이디
    client.post("/auth/logout")
    query = start_login(client, state)
    client.get("/auth/google/callback", params={"code": "c-2", "state": query["state"][0]})
    assert client.get("/auth/me").json()["username"] == "kim_golf"


def test_callback_rejects_wrong_state(client, monkeypatch):
    state = fake_google(monkeypatch)
    start_login(client, state)
    r = client.get("/auth/google/callback", params={"code": "c-1", "state": "forged"})
    assert r.headers["location"] == "/?login_error=google"
    assert client.get("/auth/me").json()["username"] is None


def test_callback_without_login_cookie_is_rejected(client, monkeypatch):
    fake_google(monkeypatch)
    r = client.get("/auth/google/callback", params={"code": "c-1", "state": "s"})
    assert r.headers["location"] == "/?login_error=google"


def test_callback_cancel_shows_cancel_message(client, monkeypatch):
    fake_google(monkeypatch)
    r = client.get("/auth/google/callback", params={"error": "access_denied"})
    assert r.headers["location"] == "/?login_error=google_cancel"


def test_redirect_uri_uses_forwarded_host_behind_worker(client, monkeypatch):
    state = fake_google(monkeypatch)
    r = client.get("/auth/google/login", headers={"x-forwarded-host": "swingkid.example.workers.dev", "x-forwarded-proto": "https"})
    query = parse_qs(urlparse(r.headers["location"]).query)
    assert query["redirect_uri"] == ["https://swingkid.example.workers.dev/auth/google/callback"]


def test_redirect_uri_env_override(client, monkeypatch):
    google = GoogleOAuth(CLIENT_ID, "secret", redirect_uri="https://fixed.example/auth/google/callback")
    monkeypatch.setattr(app_module, "get_google", lambda: google)
    r = client.get("/auth/google/login")
    assert parse_qs(urlparse(r.headers["location"]).query)["redirect_uri"] == ["https://fixed.example/auth/google/callback"]
