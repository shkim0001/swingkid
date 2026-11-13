"""코칭 내역 — 로그인 계정별 대화 목록(화면의 코칭 카드)을 서버에 저장합니다.

- 대화방(thread_id)마다 주인(아이디)을 하나 정합니다. 처음 쓴 계정이 주인이 되고, 다른 계정은 그 대화방을
  읽거나 덮어쓰거나 /query 로 이어 쓸 수 없습니다(단기 기억 체크포인터도 thread_id 로 이어지므로).
- 내용은 화면이 만든 JSON(질문·답변·첨부 미리보기)을 그대로 저장합니다. 영상 원본은 저장하지 않습니다(브라우저 IndexedDB).
- 게스트 내역은 지금처럼 브라우저(localStorage)에만 남고 여기에는 오지 않습니다.

저장소: 오프라인은 메모리 SQLite, 라이브는 config.ACCOUNTS_DB 파일(계정과 같은 파일, 다른 표).
"""

from __future__ import annotations

import datetime
import json
import sqlite3
import threading

THREAD_ID_PATTERN = r"^[0-9A-Za-z_-]{1,64}$"


class ThreadOwnedByOther(PermissionError):
    """다른 계정의 대화방."""


class HistoryStore:
    def __init__(self, path: str = ":memory:"):
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS threads (
                    id         TEXT PRIMARY KEY,
                    owner      TEXT NOT NULL,
                    data       TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS threads_owner ON threads(owner)")

    @staticmethod
    def _now() -> str:
        return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    def owner_of(self, thread_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT owner FROM threads WHERE id = ?", (thread_id,)).fetchone()
        return row[0] if row else None

    def claim(self, thread_id: str, owner: str) -> None:
        """주인이 없으면 owner 를 주인으로 정합니다. 이미 다른 계정 것이면 ThreadOwnedByOther."""
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO threads (id, owner, data, updated_at) VALUES (?, ?, NULL, ?)",
                (thread_id, owner, self._now()),
            )
            row = self._conn.execute("SELECT owner FROM threads WHERE id = ?", (thread_id,)).fetchone()
        if row[0] != owner:
            raise ThreadOwnedByOther(thread_id)

    def put(self, owner: str, thread_id: str, data: dict) -> None:
        self.claim(thread_id, owner)
        with self._lock:
            self._conn.execute(
                "UPDATE threads SET data = ?, updated_at = ? WHERE id = ? AND owner = ?",
                (json.dumps(data, ensure_ascii=False), self._now(), thread_id, owner),
            )

    def list(self, owner: str) -> list[dict]:
        """내 코칭 내역(최근에 바뀐 것이 앞). 내용 없이 주인만 정해진 대화방은 빼고 돌려줍니다."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM threads WHERE owner = ? AND data IS NOT NULL ORDER BY updated_at DESC", (owner,)
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def list_all(self, exclude_owner: str | None = None) -> list[dict]:
        """[관리자] 모든 계정의 코칭 내역 [{owner, thread}] (최근에 바뀐 것이 앞)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT owner, data FROM threads WHERE data IS NOT NULL AND owner IS NOT ? ORDER BY updated_at DESC",
                (exclude_owner,),
            ).fetchall()
        return [{"owner": owner, "thread": json.loads(data)} for owner, data in rows]

    def delete_any(self, thread_id: str) -> int:
        """[관리자] 누구 것이든 지웁니다. 주인 기록은 남깁니다."""
        with self._lock:
            return self._conn.execute(
                "UPDATE threads SET data = NULL, updated_at = ? WHERE id = ? AND data IS NOT NULL", (self._now(), thread_id)
            ).rowcount

    def delete(self, owner: str, thread_id: str) -> int:
        """내 대화방 하나를 지웁니다. 주인 기록은 남겨 다른 계정이 같은 thread_id 를 가져가지 못하게 합니다."""
        with self._lock:
            return self._conn.execute(
                "UPDATE threads SET data = NULL, updated_at = ? WHERE id = ? AND owner = ? AND data IS NOT NULL",
                (self._now(), thread_id, owner),
            ).rowcount

    def clear(self, owner: str) -> int:
        with self._lock:
            return self._conn.execute(
                "UPDATE threads SET data = NULL, updated_at = ? WHERE owner = ? AND data IS NOT NULL", (self._now(), owner)
            ).rowcount
