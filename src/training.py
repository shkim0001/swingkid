"""훈련 관리 — 계정별 연습 항목과 기록 (SERVICE.md 6장).

- 자동 추천: 사진·영상 판정의 문제 코드마다 CATALOG 에 정해 둔 연습 항목을 넣습니다. 규칙(코드)으로만 정하고
  LLM 을 쓰지 않습니다. 같은 항목이 이미 진행 중이면 다시 넣지 않습니다(보관하면 다시 추천될 수 있음).
- 직접 추가: 이름·단위(회·분·개)·1회 목표.
- 기록: 항목마다 날짜·횟수·메모. 하루에 여러 번 남길 수 있습니다.
- 집계(이번 주 연습일·연속 연습일·누적)는 모두 이 코드가 계산합니다. 주는 월요일~일요일입니다.

저장소: 오프라인은 메모리 SQLite, 라이브는 config.ACCOUNTS_DB 파일(계정과 같은 파일, 다른 표).
"""

from __future__ import annotations

import datetime
import secrets
import sqlite3
import threading

from src import config

UNITS = ("회", "분", "개")
TITLE_MAX = 40
TARGET_MAX = 1000
COUNT_MAX = 10000
MEMO_MAX = 300
WEEKLY_TARGET_DAYS = 3   # 주간 목표: 일주일에 연습한 날 수 (SERVICE.md 6장 제안값)

# 문제 코드 → 연습 항목. 내용은 골프 지식 문서(data/docs)의 '연습 방법'에서 가져왔습니다.
# metric 은 장기 기억 판정 기록(src/memory.py)의 각도 이름이라, 각도 변화 그래프에 씁니다.
CATALOG = [
    {"key": "spine_club_setup", "issue_code": config.SPINE_TILT, "metric": "spine_tilt",
     "title": "클럽을 엉덩이 옆에 대고 셋업 반복", "unit": "회", "target": 10, "doc_id": "address.md"},
    {"key": "knee_mirror_setup", "issue_code": config.KNEE_FLEX, "metric": "knee_flex",
     "title": "거울·휴대폰 옆에 두고 셋업 반복(무릎 각도)", "unit": "회", "target": 10, "doc_id": "address.md"},
    {"key": "lead_arm_top_check", "issue_code": config.BENT_LEAD_ARM, "metric": "lead_arm",
     "title": "백스윙 정점에서 멈추고 왼팔 확인", "unit": "회", "target": 10, "doc_id": "backswing.md"},
    {"key": "lead_arm_towel_half", "issue_code": config.BENT_LEAD_ARM, "metric": "lead_arm",
     "title": "수건을 겨드랑이에 끼우고 하프 스윙", "unit": "회", "target": 10, "doc_id": "backswing.md"},
]

_DRILL_COLUMNS = ("id", "owner", "key", "title", "unit", "target", "source", "issue_code", "metric", "doc_id", "created_at", "archived")
_LOG_COLUMNS = ("id", "drill_id", "owner", "day", "count", "memo", "created_at")


class TrainingError(ValueError):
    """입력이 규칙에 맞지 않거나 쓸 수 없는 항목. 메시지는 화면에 그대로 보여도 되는 문장입니다."""


def week_bounds(today: datetime.date) -> tuple[datetime.date, datetime.date]:
    """today 가 속한 주의 월요일과 일요일."""
    start = today - datetime.timedelta(days=today.weekday())
    return start, start + datetime.timedelta(days=6)


def streak(days: set[datetime.date], today: datetime.date) -> int:
    """연속 연습일. 오늘 아직 안 했어도 어제까지 이어졌으면 어제부터 셉니다."""
    cursor = today if today in days else today - datetime.timedelta(days=1)
    count = 0
    while cursor in days:
        count += 1
        cursor -= datetime.timedelta(days=1)
    return count


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _check_count(count: int) -> None:
    if not isinstance(count, int) or isinstance(count, bool) or not (1 <= count <= COUNT_MAX):
        raise TrainingError(f"횟수는 1~{COUNT_MAX} 사이 정수입니다.")


class TrainingStore:
    def __init__(self, path: str = ":memory:"):
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS training_drills (
                    id         TEXT PRIMARY KEY,
                    owner      TEXT NOT NULL,
                    key        TEXT,
                    title      TEXT NOT NULL,
                    unit       TEXT NOT NULL,
                    target     INTEGER NOT NULL,
                    source     TEXT NOT NULL,
                    issue_code TEXT,
                    metric     TEXT,
                    doc_id     TEXT,
                    created_at TEXT NOT NULL,
                    archived   INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS training_drills_owner ON training_drills(owner);
                CREATE TABLE IF NOT EXISTS training_logs (
                    id         TEXT PRIMARY KEY,
                    drill_id   TEXT NOT NULL REFERENCES training_drills(id),
                    owner      TEXT NOT NULL,
                    day        TEXT NOT NULL,
                    count      INTEGER NOT NULL,
                    memo       TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS training_logs_owner ON training_logs(owner, day);
                """
            )

    # ── 항목 ──

    def _insert_drill(self, owner: str, **fields) -> dict:
        drill = {"id": secrets.token_hex(8), "owner": owner, "key": None, "issue_code": None, "metric": None,
                 "doc_id": None, "created_at": _now(), "archived": 0, **fields}
        with self._lock:
            self._conn.execute(
                f"INSERT INTO training_drills ({', '.join(_DRILL_COLUMNS)}) VALUES ({', '.join('?' * len(_DRILL_COLUMNS))})",
                tuple(drill[c] for c in _DRILL_COLUMNS),
            )
        return self._public_drill(drill)

    @staticmethod
    def _public_drill(row: dict) -> dict:
        d = dict(row)
        d["archived"] = bool(d["archived"])
        return d

    def recommend(self, owner: str, issue_codes: list[str]) -> list[dict]:
        """문제 코드에 맞는 연습 항목을 넣고, 새로 넣은 항목을 돌려줍니다(진행 중인 같은 항목은 건너뜀)."""
        codes = set(issue_codes)
        with self._lock:
            active = {r[0] for r in self._conn.execute(
                "SELECT key FROM training_drills WHERE owner = ? AND archived = 0 AND key IS NOT NULL", (owner,))}
        added = []
        for item in CATALOG:
            if item["issue_code"] in codes and item["key"] not in active:
                added.append(self._insert_drill(owner, source="auto", **{k: item[k] for k in
                             ("key", "title", "unit", "target", "issue_code", "metric", "doc_id")}))
                active.add(item["key"])
        return added

    def add_custom(self, owner: str, title: str, unit: str, target: int) -> dict:
        title = (title or "").strip()
        if not (1 <= len(title) <= TITLE_MAX):
            raise TrainingError(f"항목 이름은 1~{TITLE_MAX}자입니다.")
        if unit not in UNITS:
            raise TrainingError("단위는 회·분·개 중 하나입니다.")
        if not isinstance(target, int) or isinstance(target, bool) or not (1 <= target <= TARGET_MAX):
            raise TrainingError(f"1회 목표는 1~{TARGET_MAX} 사이 정수입니다.")
        return self._insert_drill(owner, title=title, unit=unit, target=target, source="custom")

    def _drill_rows(self, where: str, args: tuple) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {', '.join(_DRILL_COLUMNS)} FROM training_drills WHERE {where} ORDER BY created_at, rowid", args
            ).fetchall()
        return [self._public_drill(dict(zip(_DRILL_COLUMNS, r))) for r in rows]

    def drills(self, owner: str, include_archived: bool = False) -> list[dict]:
        if include_archived:
            return self._drill_rows("owner = ?", (owner,))
        return self._drill_rows("owner = ? AND archived = 0", (owner,))

    def _get_drill(self, owner: str, drill_id: str) -> dict | None:
        rows = self._drill_rows("owner = ? AND id = ?", (owner, drill_id))
        return rows[0] if rows else None

    def archive(self, owner: str, drill_id: str) -> bool:
        """항목을 목록에서 내립니다(기록은 남김). 내 항목이 아니면 False."""
        with self._lock:
            return self._conn.execute(
                "UPDATE training_drills SET archived = 1 WHERE owner = ? AND id = ? AND archived = 0", (owner, drill_id)
            ).rowcount == 1

    # ── 기록 ──

    def add_log(self, owner: str, drill_id: str, day: datetime.date, count: int, memo: str = "",
                today: datetime.date | None = None) -> dict:
        today = today or datetime.date.today()
        drill = self._get_drill(owner, drill_id)
        if drill is None or drill["archived"]:
            raise TrainingError("기록할 수 없는 항목입니다.")
        if not isinstance(day, datetime.date) or day > today:
            raise TrainingError("오늘이나 지난 날짜만 기록할 수 있습니다.")
        _check_count(count)
        memo = (memo or "").strip()
        if len(memo) > MEMO_MAX:
            raise TrainingError(f"메모는 {MEMO_MAX}자까지입니다.")
        log = {"id": secrets.token_hex(8), "drill_id": drill_id, "owner": owner, "day": day.isoformat(),
               "count": count, "memo": memo, "created_at": _now()}
        with self._lock:
            self._conn.execute(
                f"INSERT INTO training_logs ({', '.join(_LOG_COLUMNS)}) VALUES ({', '.join('?' * len(_LOG_COLUMNS))})",
                tuple(log[c] for c in _LOG_COLUMNS),
            )
        return log

    def logs(self, owner: str, drill_id: str | None = None, limit: int = 200) -> list[dict]:
        """기록(최신이 앞)."""
        sql = f"SELECT {', '.join(_LOG_COLUMNS)} FROM training_logs WHERE owner = ?"
        args: tuple = (owner,)
        if drill_id is not None:
            sql += " AND drill_id = ?"
            args += (drill_id,)
        with self._lock:
            rows = self._conn.execute(sql + " ORDER BY day DESC, created_at DESC, rowid DESC LIMIT ?", args + (limit,)).fetchall()
        return [dict(zip(_LOG_COLUMNS, r)) for r in rows]

    def delete_log(self, owner: str, log_id: str) -> bool:
        with self._lock:
            return self._conn.execute("DELETE FROM training_logs WHERE owner = ? AND id = ?", (owner, log_id)).rowcount == 1

    # ── 집계 ──

    def summary(self, owner: str, today: datetime.date | None = None) -> dict:
        """진행 중 항목마다 이번 주 연습일·횟수, 연속 연습일, 오늘·누적 횟수. 전체 이번 주 연습일과 연속 연습일."""
        today = today or datetime.date.today()
        start, end = week_bounds(today)
        drills = self.drills(owner)
        with self._lock:
            rows = self._conn.execute("SELECT drill_id, day, count FROM training_logs WHERE owner = ?", (owner,)).fetchall()
        by_drill: dict[str, list[tuple[datetime.date, int]]] = {}
        for drill_id, day, count in rows:
            by_drill.setdefault(drill_id, []).append((datetime.date.fromisoformat(day), count))
        all_days: set[datetime.date] = set()
        items = []
        for d in drills:
            entries = by_drill.get(d["id"], [])
            days = {day for day, _ in entries}
            all_days |= days
            week = [(day, n) for day, n in entries if start <= day <= end]
            items.append({
                **d,
                "week_days": len({day for day, _ in week}),
                "week_count": sum(n for _, n in week),
                "today_count": sum(n for day, n in entries if day == today),
                "total_count": sum(n for _, n in entries),
                "streak": streak(days, today),
                "last_day": max(days).isoformat() if days else None,
            })
        return {
            "today": today.isoformat(),
            "week": {"start": start.isoformat(), "end": end.isoformat()},
            "week_target_days": WEEKLY_TARGET_DAYS,
            "week_days": len({day for day in all_days if start <= day <= end}),
            "streak": streak(all_days, today),
            "drills": items,
        }

    def drill_owner(self, drill_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT owner FROM training_drills WHERE id = ?", (drill_id,)).fetchone()
        return row[0] if row else None

    def log_owner(self, log_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT owner FROM training_logs WHERE id = ?", (log_id,)).fetchone()
        return row[0] if row else None

    # ── 관리자 ──

    def owners(self) -> list[str]:
        """진행 중 항목이 있는 계정(이름순)."""
        with self._lock:
            return [r[0] for r in self._conn.execute(
                "SELECT DISTINCT owner FROM training_drills WHERE archived = 0 ORDER BY owner")]

    def archive_any(self, drill_id: str) -> bool:
        with self._lock:
            return self._conn.execute(
                "UPDATE training_drills SET archived = 1 WHERE id = ? AND archived = 0", (drill_id,)
            ).rowcount == 1

    def delete_log_any(self, log_id: str) -> bool:
        with self._lock:
            return self._conn.execute("DELETE FROM training_logs WHERE id = ?", (log_id,)).rowcount == 1
