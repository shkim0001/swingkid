"""Google 계정 로그인 — OAuth 2.0 인가 코드 흐름 + OpenID Connect ID 토큰 (새 라이브러리 없이 httpx 만 사용).

흐름
1. auth_url(): Google 동의 화면 주소. state(위조 요청 방지)와 nonce(ID 토큰 재사용 방지)를 함께 보냅니다.
2. exchange(): 콜백으로 받은 code 를 Google 토큰 주소에 보내 ID 토큰을 받습니다(client_secret 사용).
3. parse_id_token(): ID 토큰에서 sub·email 을 꺼내고 iss·aud·exp·nonce 를 코드로 검사합니다.

서명 검사를 따로 하지 않는 이유: ID 토큰을 브라우저가 아니라 서버가 Google 토큰 주소에서 HTTPS 로 직접 받았기
때문입니다(OpenID Connect Core 3.1.3.7 — TLS 로 토큰 주소에서 직접 받은 ID 토큰은 TLS 서버 검증으로 서명 검사를
대신할 수 있음). 그래서 이 ID 토큰을 다른 경로(브라우저 입력 등)로 받으면 안 됩니다.

설정(.env): GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, 선택 GOOGLE_REDIRECT_URI(배포 주소를 고정할 때).
"""

from __future__ import annotations

import base64
import json
import os
import time
from urllib.parse import urlencode

from src.accounts.auth import AuthError
from src.accounts.schemas import GoogleIdentity

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
SCOPE = "openid email"
CALLBACK_PATH = "/auth/google/callback"
CLOCK_SKEW = 60   # 서버 시계 차이 허용(초)
TIMEOUT = 10      # Google 토큰 주소 호출 제한(초)


def _b64url_json(part: str) -> dict:
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def parse_id_token(id_token: str, client_id: str, nonce: str, now: float | None = None) -> GoogleIdentity:
    """Google 토큰 주소에서 직접 받은 ID 토큰을 검사하고 sub·email 을 꺼냅니다. 맞지 않으면 AuthError."""
    try:
        claims = _b64url_json(id_token.split(".")[1])
    except (IndexError, ValueError, AttributeError):
        raise AuthError("Google 로그인 응답을 읽지 못했습니다.") from None
    now = time.time() if now is None else now
    aud = claims.get("aud")
    if claims.get("iss") not in ISSUERS:
        raise AuthError("Google 로그인 응답의 발급자가 맞지 않습니다.")
    if aud != client_id and not (isinstance(aud, list) and client_id in aud):
        raise AuthError("Google 로그인 응답이 이 서비스용이 아닙니다.")
    if not isinstance(claims.get("exp"), (int, float)) or claims["exp"] + CLOCK_SKEW < now:
        raise AuthError("Google 로그인이 만료되었습니다. 다시 시도해 주세요.")
    if not nonce or claims.get("nonce") != nonce:
        raise AuthError("Google 로그인 요청이 맞지 않습니다. 다시 시도해 주세요.")
    if not claims.get("sub"):
        raise AuthError("Google 계정 정보를 확인하지 못했습니다.")
    return GoogleIdentity(sub=str(claims["sub"]), email=str(claims.get("email") or ""))


class GoogleOAuth:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str | None = None, transport=None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri   # 없으면 요청 주소로 정합니다(api/app.py)
        self._transport = transport        # 테스트에서 httpx.MockTransport 를 넣습니다

    @classmethod
    def from_env(cls) -> "GoogleOAuth | None":
        """.env 에 클라이언트 ID·비밀이 모두 있으면 만들고, 없으면 None(Google 로그인 끔)."""
        client_id, secret = os.getenv("GOOGLE_CLIENT_ID", "").strip(), os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
        if not client_id or not secret:
            return None
        return cls(client_id, secret, os.getenv("GOOGLE_REDIRECT_URI", "").strip() or None)

    def auth_url(self, redirect_uri: str, state: str, nonce: str) -> str:
        query = {
            "client_id": self.client_id, "redirect_uri": redirect_uri, "response_type": "code",
            "scope": SCOPE, "state": state, "nonce": nonce, "prompt": "select_account",
        }
        return f"{AUTH_ENDPOINT}?{urlencode(query)}"

    def exchange(self, code: str, redirect_uri: str, nonce: str) -> GoogleIdentity:
        """인가 코드를 ID 토큰으로 바꾸고 검사합니다. 네트워크·Google 오류도 AuthError 로 바꿉니다."""
        import httpx

        data = {
            "code": code, "client_id": self.client_id, "client_secret": self.client_secret,
            "redirect_uri": redirect_uri, "grant_type": "authorization_code",
        }
        try:
            with httpx.Client(timeout=TIMEOUT, transport=self._transport) as client:
                resp = client.post(TOKEN_ENDPOINT, data=data)
        except httpx.HTTPError:
            raise AuthError("Google 에 연결하지 못했습니다. 잠시 뒤 다시 시도해 주세요.") from None
        if resp.status_code != 200:
            raise AuthError("Google 로그인을 마치지 못했습니다. 다시 시도해 주세요.")
        try:
            id_token = resp.json()["id_token"]
        except (ValueError, KeyError):
            raise AuthError("Google 로그인 응답을 읽지 못했습니다.") from None
        return parse_id_token(id_token, self.client_id, nonce)
