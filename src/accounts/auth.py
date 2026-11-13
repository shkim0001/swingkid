"""계정 — 아이디·비밀번호 가입, 로그인, 로그아웃, Google 계정 연결 (표준 라이브러리만 사용).

- 비밀번호는 hashlib.scrypt(솔트 16바이트)로 해싱해 저장합니다. 원문은 어디에도 남기지 않습니다.
- 로그인하면 secrets 로 만든 세션 토큰을 돌려주고, 서버는 토큰의 SHA-256 만 저장합니다(DB 가 새도 토큰을 쓸 수 없게).
- 세션은 SESSION_DAYS 일 뒤 만료됩니다. 로그아웃하면 그 토큰을 지웁니다.
- 아이디·비밀번호 규칙 판정은 모두 이 코드가 합니다(LLM 을 쓰지 않음).

Google 계정: Google 고유 ID(sub)를 google_accounts 표에 계정과 묶어 둡니다. 처음이면 이메일 앞부분으로 아이디를 만들고
(google_username), 비밀번호 칸은 비워 비밀번호 로그인이 되지 않게 합니다. Google 과 통신하는 코드는 google.py 에 있습니다.

관리자: 아이디 'admin' 하나. 화면에서는 가입할 수 없고(대소문자 무관), 서버가 켜질 때 .env 의 ADMIN_PASSWORD 로
만들거나 비밀번호를 맞춥니다(ensure_admin). 관리자는 모든 계정의 코칭을 보고 지울 수 있습니다(이어 쓰기는 불가).

저장소: 오프라인은 메모리 SQLite, 라이브는 config.ACCOUNTS_DB 파일.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading

from src.accounts.avatar import is_valid as avatar_is_valid
from src.accounts.avatar import new_avatar

USERNAME_PATTERN = r"^[0-9A-Za-z가-힣_-]{2,32}$"
PASSWORD_MIN, PASSWORD_MAX = 8, 128
SESSION_DAYS = 30
ADMIN_USERNAME = "admin"
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}
USERNAME_MAX = 32
GOOGLE_FALLBACK_NAME = "golfer"   # 이메일 앞부분에 쓸 수 있는 글자가 2자 미만일 때


class AuthError(ValueError):
    """가입·로그인 실패. 메시지는 화면에 그대로 보여도 되는 문장입니다."""


def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def check_username(username: str) -> None:
    if not re.fullmatch(USERNAME_PATTERN, username or ""):
        raise AuthError("아이디는 띄어쓰기 없이 한글·영문·숫자·-·_ 2~32자입니다.")


def check_password(password: str) -> None:
    if not (PASSWORD_MIN <= len(password or "") <= PASSWORD_MAX):
        raise AuthError(f"비밀번호는 {PASSWORD_MIN}~{PASSWORD_MAX}자입니다.")


def google_username_base(email: str) -> str:
    """이메일 앞부분을 아이디 규칙에 맞게 바꿉니다. ('kim.golf+1@gmail.com' → 'kim_golf_1')

    허용 문자(영문·숫자·한글·-·_) 밖은 '_' 로 바꾸고, 연달아 나오거나 앞뒤에 붙은 '_' 는 정리합니다.
    뒤에 숫자를 붙일 자리를 남기려고 28자로 자릅니다. 2자 미만이면 GOOGLE_FALLBACK_NAME 을 씁니다.
    """
    local = (email or "").split("@", 1)[0]
    name = re.sub(r"[^0-9A-Za-z가-힣_-]", "_", local)
    name = re.sub(r"_+", "_", name).strip("_-")[: USERNAME_MAX - 4]
    return name if len(name) >= 2 else GOOGLE_FALLBACK_NAME


class AccountStore:
    def __init__(self, path: str = ":memory:"):
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    username   TEXT PRIMARY KEY,
                    pw_salt    BLOB NOT NULL,
                    pw_hash    BLOB NOT NULL,
                    created_at TEXT NOT NULL,
                    avatar     TEXT
                );
                CREATE TABLE IF NOT EXISTS google_accounts (
                    sub        TEXT PRIMARY KEY,
                    username   TEXT NOT NULL UNIQUE REFERENCES users(username),
                    email      TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_sha  TEXT PRIMARY KEY,
                    username   TEXT NOT NULL REFERENCES users(username),
                    expires_at TEXT NOT NULL
                );
                """
            )
            # 캐릭터 아이콘 칸이 생기기 전에 만든 DB 에는 칸을 덧붙입니다.
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(users)")}
            if "avatar" not in columns:
                self._conn.execute("ALTER TABLE users ADD COLUMN avatar TEXT")

    def signup(self, username: str, password: str) -> None:
        check_username(username)
        check_password(password)
        if username.lower() == ADMIN_USERNAME:
            raise AuthError("쓸 수 없는 아이디입니다.")
        self._create(username, password)

    def ensure_admin(self, password: str) -> None:
        """관리자 계정을 만들거나, 이미 있으면 비밀번호를 password 로 맞춥니다(.env 가 기준)."""
        check_password(password)
        with self._lock:
            exists = self._conn.execute("SELECT 1 FROM users WHERE username = ?", (ADMIN_USERNAME,)).fetchone()
        if exists is None:
            self._create(ADMIN_USERNAME, password)
            return
        salt = secrets.token_bytes(16)
        with self._lock:
            self._conn.execute(
                "UPDATE users SET pw_salt = ?, pw_hash = ? WHERE username = ?",
                (salt, _hash_password(password, salt), ADMIN_USERNAME),
            )

    def _create(self, username: str, password: str) -> None:
        salt = secrets.token_bytes(16)
        try:
            with self._lock:
                self._insert_user(username, salt, _hash_password(password, salt))
        except sqlite3.IntegrityError:
            raise AuthError("이미 쓰고 있는 아이디입니다.") from None

    def _insert_user(self, username: str, salt: bytes, pw_hash: bytes) -> None:
        """잠금을 잡은 상태에서 부릅니다."""
        self._conn.execute(
            "INSERT INTO users (username, pw_salt, pw_hash, created_at, avatar) VALUES (?, ?, ?, ?, ?)",
            (username, salt, pw_hash, _now().isoformat(timespec="seconds"),
             json.dumps(new_avatar())),   # 가입할 때 캐릭터 아이콘을 무작위로 정함
        )

    def google_login(self, sub: str, email: str) -> tuple[str, str, bool]:
        """Google 계정으로 로그인합니다. (세션 토큰, 아이디, 이번에 새로 만들었는지)를 돌려줍니다.

        같은 sub 는 이메일이 바뀌어도 같은 계정입니다. 처음이면 이메일 앞부분으로 아이디를 만들고, 이미 있거나
        관리자 아이디면 뒤에 2, 3, … 을 붙입니다. 비밀번호 해시는 빈 값이라 비밀번호 로그인은 언제나 실패합니다.
        """
        if not sub:
            raise AuthError("Google 계정 정보를 확인하지 못했습니다.")
        with self._lock:
            row = self._conn.execute("SELECT username FROM google_accounts WHERE sub = ?", (sub,)).fetchone()
            created = row is None
            if created:
                username = self._free_username(google_username_base(email))
                self._insert_user(username, secrets.token_bytes(16), b"")
                self._conn.execute(
                    "INSERT INTO google_accounts (sub, username, email, created_at) VALUES (?, ?, ?, ?)",
                    (sub, username, email, _now().isoformat(timespec="seconds")),
                )
            else:
                username = row[0]
                self._conn.execute("UPDATE google_accounts SET email = ? WHERE sub = ?", (email, sub))
        return self._new_session(username), username, created

    def _free_username(self, base: str) -> str:
        """잠금을 잡은 상태에서 부릅니다. base, base2, base3 … 가운데 비어 있는 첫 아이디."""
        for n in range(1, 10000):
            name = base if n == 1 else f"{base}{n}"
            if name.lower() == ADMIN_USERNAME:
                continue
            if self._conn.execute("SELECT 1 FROM users WHERE username = ?", (name,)).fetchone() is None:
                return name
        raise AuthError("아이디를 만들지 못했습니다.")

    def is_google(self, username: str) -> bool:
        with self._lock:
            return self._conn.execute("SELECT 1 FROM google_accounts WHERE username = ?", (username,)).fetchone() is not None

    def login(self, username: str, password: str) -> str:
        """맞으면 새 세션 토큰을 돌려줍니다. 아이디가 없든 비밀번호가 틀리든 같은 문장으로 거절합니다."""
        with self._lock:
            row = self._conn.execute("SELECT pw_salt, pw_hash FROM users WHERE username = ?", (username,)).fetchone()
        if row is None:
            _hash_password(password or "", b"\0" * 16)   # 없는 아이디도 비슷한 시간이 걸리게
            raise AuthError("아이디 또는 비밀번호가 맞지 않습니다.")
        salt, stored = row
        # Google 계정은 비밀번호 해시가 비어 있어(b"") 어떤 비밀번호로도 맞지 않습니다.
        if not hmac.compare_digest(_hash_password(password or "", salt), stored):
            raise AuthError("아이디 또는 비밀번호가 맞지 않습니다.")
        return self._new_session(username)

    def _new_session(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        expires = (_now() + datetime.timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE expires_at < ?", (_now().isoformat(timespec="seconds"),))
            self._conn.execute(
                "INSERT INTO sessions (token_sha, username, expires_at) VALUES (?, ?, ?)",
                (_token_digest(token), username, expires),
            )
        return token

    def user_for(self, token: str | None) -> str | None:
        """세션 토큰의 주인 아이디. 없거나 만료됐으면 None."""
        if not token:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT username, expires_at FROM sessions WHERE token_sha = ?", (_token_digest(token),)
            ).fetchone()
        if row is None or row[1] < _now().isoformat(timespec="seconds"):
            return None
        return row[0]

    def avatar(self, username: str) -> dict | None:
        """이 계정의 캐릭터 부품. 아이콘 기능 전에 가입한 계정은 처음 부를 때 한 번 정해 저장합니다."""
        with self._lock:
            row = self._conn.execute("SELECT avatar FROM users WHERE username = ?", (username,)).fetchone()
            if row is None:
                return None
            try:
                current = json.loads(row[0]) if row[0] else None
            except ValueError:
                current = None
            if avatar_is_valid(current):
                return current
            fresh = new_avatar()
            self._conn.execute("UPDATE users SET avatar = ? WHERE username = ?", (json.dumps(fresh), username))
            return fresh

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._conn.execute("DELETE FROM sessions WHERE token_sha = ?", (_token_digest(token),))


def is_admin(username: str | None) -> bool:
    return username == ADMIN_USERNAME


def memory_owner(username: str) -> str:
    """로그인 계정의 장기 기억 주인 키.

    예전 닉네임 방식으로 쌓인 기록(user_id=닉네임)과 섞이지 않게 접두어를 붙입니다.
    그래서 남이 쓰던 닉네임으로 가입해도 그 사람의 예전 기록이 보이지 않습니다.
    """
    return f"account:{username}"
