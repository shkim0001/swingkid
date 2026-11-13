"""훈련 관리 — 계정별 연습 항목과 기록 (SERVICE.md 6장).

- 훈련 라이브러리: data/training_library.json(문서 기반 25개 항목·8개 분야). 내 항목에 넣어 씁니다.
- 자동 추천: 사진·영상 판정의 문제 코드마다 라이브러리에서 그 코드가 붙은 항목을 넣습니다. 규칙(코드)으로만 정하고
  LLM 을 쓰지 않습니다. 같은 항목이 이미 진행 중이면 다시 넣지 않습니다(보관하면 다시 추천될 수 있음).
- 직접 추가: 이름·단위(회·분·개)·1회 목표.
- 기록: 항목마다 날짜·횟수·메모. 하루에 여러 번 남길 수 있습니다.
- 집계(이번 주 연습일·연속 연습일·누적)는 모두 이 코드가 계산합니다. 주는 월요일~일요일입니다.

저장소: 오프라인은 메모리 SQLite, 라이브는 config.ACCOUNTS_DB 파일(계정과 같은 파일, 다른 표).
"""

from __future__ import annotations

import calendar
import datetime
import json
import pathlib
import re
import secrets
import sqlite3
import threading

from src.shared import config

UNITS = ("회", "분", "개")
TITLE_MAX = 40
TARGET_MAX = 1000
COUNT_MAX = 10000
MEMO_MAX = 300
WEEKLY_TARGET_DAYS = 3   # 주간 목표: 일주일에 연습한 날 수 (SERVICE.md 6장 제안값)
ROUTINE_MAX_DAYS = 366   # 요일 루틴 최대 기간
# 판정 기반 추천 플랜 (SERVICE.md 6장 제안값): 오늘부터 2주, 월·수·금
PLAN_DAYS = 14
PLAN_WEEKDAYS = [0, 2, 4]

LIBRARY_PATH = config.PROJECT_ROOT / "data" / "training_library.json"
LOCATIONS = ("집", "연습장", "어디서나")
LEVELS = {1: "입문", 2: "기본", 3: "도전"}


def load_library(path=LIBRARY_PATH) -> dict:
    """훈련 라이브러리(data/training_library.json). 골프 지식 문서의 '연습 방법'·교정 내용을 항목으로 정리한 것.

    항목: key·category·title·how·location·minutes·equipment·level·unit·target·doc_id·issue_codes·metric.
    issue_codes 가 있으면 그 문제 코드가 나올 때 자동 추천하고, metric 은 장기 기억 판정 기록의 각도 이름입니다.
    """
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    return {"categories": data["categories"], "items": data["items"]}


LIBRARY = load_library()
LIBRARY_BY_KEY = {item["key"]: item for item in LIBRARY["items"]}
# 자동 추천에 쓰는 항목(문제 코드가 붙은 것)
CATALOG = [item for item in LIBRARY["items"] if item["issue_codes"]]

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


def month_grid(year: int, month: int) -> list[list[datetime.date]]:
    """그 달을 덮는 월요일 시작 주들(앞뒤 달 날짜 포함, 4~6주)."""
    first = datetime.date(year, month, 1)
    last = datetime.date(year, month, calendar.monthrange(year, month)[1])
    start = first - datetime.timedelta(days=first.weekday())
    end = last + datetime.timedelta(days=6 - last.weekday())
    days = [start + datetime.timedelta(days=i) for i in range((end - start).days + 1)]
    return [days[i : i + 7] for i in range(0, len(days), 7)]


def _days(start: datetime.date, end: datetime.date):
    day = start
    while day <= end:
        yield day
        day += datetime.timedelta(days=1)


def _rate(days: list[dict], until: datetime.date) -> dict:
    """달성률: until(오늘)까지 계획한 것 중 완료한 비율. 계획이 없으면 rate=None."""
    planned = [p for d in days if d["date"] <= until.isoformat() for p in d["planned"]]
    done = sum(1 for p in planned if p["status"] == "done")
    return {"planned": len(planned), "done": done, "rate": round(done / len(planned), 3) if planned else None}


METRICS = {   # 각도 이름 → 화면 이름과 교육용 기준(src/shared/config.py). 왼팔은 아래 기준만 있음
    "spine_tilt": {"label": "척추 앞기울기", "lo": config.SPINE_TILT_RANGE[0], "hi": config.SPINE_TILT_RANGE[1]},
    "knee_flex": {"label": "무릎 각도", "lo": config.KNEE_FLEX_RANGE[0], "hi": config.KNEE_FLEX_RANGE[1]},
    "lead_arm": {"label": "정점 왼팔 각도", "lo": config.LEAD_ARM_MIN, "hi": None},
}


def angle_trend(records: list[dict], metric: str) -> dict:
    """장기 기억 판정 기록(최신이 앞)에서 한 각도의 추이를 오래된 순으로 계산합니다(기준 안·밖, 처음 대비 변화)."""
    if metric not in METRICS:
        raise ValueError(f"알 수 없는 각도입니다: {metric}")
    m = METRICS[metric]

    def ok(v: float) -> bool:
        return v >= m["lo"] and (m["hi"] is None or v <= m["hi"])

    points = [
        {"at": r["at"], "kind": r.get("kind"), "value": float(r["metrics"][metric]), "in_range": ok(float(r["metrics"][metric]))}
        for r in sorted(records, key=lambda r: r["at"])
        if metric in r.get("metrics", {})
    ]
    first, last = (points[0]["value"], points[-1]["value"]) if points else (None, None)
    return {
        "metric": metric, "label": m["label"], "range": {"lo": m["lo"], "hi": m["hi"]}, "points": points,
        "first": first, "last": last, "change": round(last - first, 1) if points else None,
        "in_range_count": sum(p["in_range"] for p in points),
        "latest_in_range": points[-1]["in_range"] if points else None,
    }


PRACTICE_DOC_ID = "training://my-practice"   # 연습 요약을 근거로 쓸 때 contexts·sources 에 남는 이름
SRC_KO = {"auto": "판정 추천", "library": "라이브러리", "custom": "직접 추가"}
WD_KO = "월화수목금토일"

# "요즘 연습 어때?", "이번 주 훈련 기록", "달성률" 처럼 사용자의 연습 현황을 묻는 질문.
# "연습장에서 어떻게 연습해요", "퍼팅 연습 방법" 같은 방법 질문은 넣지 않습니다(지식 문서가 답함).
_PRACTICE_Q = re.compile(
    r"(내|나의|제)\s*(연습|훈련)|(연습|훈련)\s*(기록|현황|량|어때|어땠|잘\s*하고|얼마나|몇\s*번|많이|계획|일정|플랜|달성)"
    r"|(연습|훈련)(을|를|은|는)\s*(얼마나|몇\s*번|잘)|달성\s*률|이번\s*(주|달)\s*(연습|훈련)|루틴\s*(어때|잘|지키)"
)


def is_practice_question(question: str) -> bool:
    return bool(_PRACTICE_Q.search(question))


def practice_summary(store: "TrainingStore", owner: str, today: datetime.date | None = None) -> str:
    """코칭 모델에 근거로 넘길 연습 요약(모두 코드가 집계). 항목도 기록도 없으면 빈 문자열."""
    today = today or datetime.date.today()
    s = store.summary(owner, today)
    logs = store.logs(owner, limit=5)
    if not s["drills"] and not logs:
        return ""
    week_start, week_end = week_bounds(today)
    week = store._schedule(owner, week_start, week_end, today)
    week_rate = _rate(week, today)
    missed = [p["title"] for d in week for p in d["planned"] if p["status"] == "missed"]
    lines = [
        f"이 사용자의 연습 기록 (기준일 {today.isoformat()}, 이번 주 {week_start.isoformat()}~{week_end.isoformat()}, 코드가 집계):",
        f"- 이번 주 연습한 날 {s['week_days']}일 (주간 목표 {s['week_target_days']}일), 연속 연습 {s['streak']}일",
        f"- 이번 주 계획 달성 {week_rate['done']}/{week_rate['planned']}회" if week_rate["planned"] else "- 이번 주 잡힌 계획 없음",
    ]
    if missed:
        lines.append(f"- 이번 주 계획했지만 기록이 없는 것: {', '.join(missed)}")
    routines = store.routines(owner)
    for d in s["drills"]:
        rs = [f"{''.join(WD_KO[w] for w in r['weekdays'])} {r['start']}~{r['end']}" for r in routines if r["drill_id"] == d["id"]]
        lines.append(
            f"- 항목 '{d['title']}' ({SRC_KO.get(d['source'], d['source'])}, 1회 목표 {d['target']}{d['unit']}"
            + (f", 근거 {d['doc_id']}" if d["doc_id"] else "") + "): "
            + f"이번 주 {d['week_days']}일·{d['week_count']}{d['unit']}, 누적 {d['total_count']}{d['unit']}, "
            + f"마지막 연습 {d['last_day'] or '없음'}" + (f", 루틴 {', '.join(rs)}" if rs else "")
        )
    titles = {d["id"]: (d["title"], d["unit"]) for d in store.drills(owner, include_archived=True)}
    if logs:
        lines.append("최근 기록:")
        for log in logs:
            title, unit = titles.get(log["drill_id"], ("(지운 항목)", ""))
            lines.append(f"- {log['day']} {title} {log['count']}{unit}" + (f" (메모: {log['memo']})" if log["memo"] else ""))
    return "\n".join(lines)


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
                -- 일정: 날짜 배치(같은 날 같은 항목은 하나), 요일 반복 루틴(기간), 루틴 하루 빼기
                CREATE TABLE IF NOT EXISTS training_plans (
                    id         TEXT PRIMARY KEY,
                    owner      TEXT NOT NULL,
                    drill_id   TEXT NOT NULL REFERENCES training_drills(id),
                    day        TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (owner, drill_id, day)
                );
                CREATE TABLE IF NOT EXISTS training_routines (
                    id         TEXT PRIMARY KEY,
                    owner      TEXT NOT NULL,
                    drill_id   TEXT NOT NULL REFERENCES training_drills(id),
                    weekdays   TEXT NOT NULL,
                    start_day  TEXT NOT NULL,
                    end_day    TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS training_proposals (
                    id          TEXT PRIMARY KEY,
                    owner       TEXT NOT NULL,
                    issue_codes TEXT NOT NULL,
                    drill_keys  TEXT NOT NULL,
                    weekdays    TEXT NOT NULL,
                    start_day   TEXT NOT NULL,
                    end_day     TEXT NOT NULL,
                    status      TEXT NOT NULL,
                    created_at  TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS training_skips (
                    owner      TEXT NOT NULL,
                    routine_id TEXT NOT NULL REFERENCES training_routines(id),
                    day        TEXT NOT NULL,
                    PRIMARY KEY (routine_id, day)
                );
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
            matched = [c for c in item["issue_codes"] if c in codes]
            if matched and item["key"] not in active:
                added.append(self._insert_from_library(owner, item, source="auto", issue_code=matched[0]))
                active.add(item["key"])
        return added

    def _insert_from_library(self, owner: str, item: dict, source: str, issue_code: str | None = None) -> dict:
        return self._insert_drill(owner, source=source, issue_code=issue_code,
                                  **{k: item[k] for k in ("key", "title", "unit", "target", "metric", "doc_id")})

    def add_from_library(self, owner: str, key: str) -> dict:
        """라이브러리 항목을 내 항목에 넣습니다. 이미 진행 중이면 그 항목을 그대로 돌려줍니다."""
        item = LIBRARY_BY_KEY.get(key)
        if item is None:
            raise TrainingError("라이브러리에 없는 항목입니다.")
        for d in self.drills(owner):
            if d["key"] == key:
                return d
        return self._insert_from_library(owner, item, source="library")

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

    # ── 일정: 날짜 배치·요일 루틴·하루 빼기 ──

    def _active_drill(self, owner: str, drill_id: str) -> dict:
        drill = self._get_drill(owner, drill_id)
        if drill is None or drill["archived"]:
            raise TrainingError("일정에 넣을 수 없는 항목입니다.")
        return drill

    def add_plan(self, owner: str, drill_id: str, day: datetime.date) -> dict:
        """그 날 계획에 항목을 넣습니다. 이미 있으면 그 계획을 돌려줍니다(같은 날 같은 항목은 하나)."""
        self._active_drill(owner, drill_id)
        if not isinstance(day, datetime.date):
            raise TrainingError("날짜가 올바르지 않습니다.")
        plan = {"id": secrets.token_hex(8), "owner": owner, "drill_id": drill_id, "day": day.isoformat(), "created_at": _now()}
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO training_plans (id, owner, drill_id, day, created_at) VALUES (?, ?, ?, ?, ?)",
                tuple(plan.values()),
            )
            row = self._conn.execute(
                "SELECT id, created_at FROM training_plans WHERE owner = ? AND drill_id = ? AND day = ?",
                (owner, drill_id, plan["day"]),
            ).fetchone()
        return {**plan, "id": row[0], "created_at": row[1]}

    def delete_plan(self, owner: str, plan_id: str) -> bool:
        with self._lock:
            return self._conn.execute("DELETE FROM training_plans WHERE owner = ? AND id = ?", (owner, plan_id)).rowcount == 1

    def add_routine(self, owner: str, drill_id: str, weekdays: list[int], start: datetime.date, end: datetime.date) -> dict:
        """요일 반복 루틴. weekdays 는 0(월)~6(일). 시작·끝 날짜를 모두 포함하며 기간은 최대 366일."""
        self._active_drill(owner, drill_id)
        wd = sorted(set(weekdays or []))
        if not wd or any(not isinstance(w, int) or isinstance(w, bool) or not 0 <= w <= 6 for w in wd):
            raise TrainingError("요일을 하나 이상 골라 주세요.")
        if not isinstance(start, datetime.date) or not isinstance(end, datetime.date) or end < start:
            raise TrainingError("끝 날짜는 시작 날짜와 같거나 뒤여야 합니다.")
        if (end - start).days > ROUTINE_MAX_DAYS:
            raise TrainingError(f"루틴 기간은 최대 {ROUTINE_MAX_DAYS}일입니다.")
        routine = {"id": secrets.token_hex(8), "owner": owner, "drill_id": drill_id, "weekdays": wd,
                   "start": start.isoformat(), "end": end.isoformat(), "created_at": _now()}
        with self._lock:
            self._conn.execute(
                "INSERT INTO training_routines (id, owner, drill_id, weekdays, start_day, end_day, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (routine["id"], owner, drill_id, ",".join(map(str, wd)), routine["start"], routine["end"], routine["created_at"]),
            )
        return routine

    def routines(self, owner: str) -> list[dict]:
        """진행 중 항목의 루틴(시작일 순)."""
        drills = {d["id"]: d for d in self.drills(owner)}
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, drill_id, weekdays, start_day, end_day, created_at FROM training_routines WHERE owner = ? ORDER BY start_day, created_at",
                (owner,),
            ).fetchall()
        return [
            {"id": r[0], "drill_id": r[1], "title": drills[r[1]]["title"], "weekdays": [int(x) for x in r[2].split(",")],
             "start": r[3], "end": r[4], "created_at": r[5]}
            for r in rows if r[1] in drills
        ]

    def delete_routine(self, owner: str, routine_id: str) -> bool:
        with self._lock:
            deleted = self._conn.execute("DELETE FROM training_routines WHERE owner = ? AND id = ?", (owner, routine_id)).rowcount == 1
            if deleted:
                self._conn.execute("DELETE FROM training_skips WHERE routine_id = ?", (routine_id,))
        return deleted

    def skip_routine_day(self, owner: str, routine_id: str, day: datetime.date) -> bool:
        """루틴에서 그 날 하루만 뺍니다. 내 루틴이 아니면 False."""
        with self._lock:
            if self._conn.execute("SELECT 1 FROM training_routines WHERE owner = ? AND id = ?", (owner, routine_id)).fetchone() is None:
                return False
            self._conn.execute("INSERT OR IGNORE INTO training_skips (owner, routine_id, day) VALUES (?, ?, ?)",
                               (owner, routine_id, day.isoformat()))
        return True

    # ── 달력: 계획을 날짜별로 펼치고 기록과 맞춰 완료·오늘·지남·예정을 판정 ──

    def _schedule(self, owner: str, start: datetime.date, end: datetime.date, today: datetime.date) -> list[dict]:
        drills = {d["id"]: d for d in self.drills(owner)}   # 내린 항목은 일정에서도 빠짐
        s, e = start.isoformat(), end.isoformat()
        with self._lock:
            plans = self._conn.execute(
                "SELECT id, drill_id, day FROM training_plans WHERE owner = ? AND day BETWEEN ? AND ?", (owner, s, e)).fetchall()
            routines = self._conn.execute(
                "SELECT id, drill_id, weekdays, start_day, end_day FROM training_routines WHERE owner = ? AND start_day <= ? AND end_day >= ?",
                (owner, e, s)).fetchall()
            skips = {(r, d) for r, d in self._conn.execute(
                "SELECT routine_id, day FROM training_skips WHERE owner = ? AND day BETWEEN ? AND ?", (owner, s, e))}
            logged = {(drill_id, day): n for drill_id, day, n in self._conn.execute(
                "SELECT drill_id, day, SUM(count) FROM training_logs WHERE owner = ? AND day BETWEEN ? AND ? GROUP BY drill_id, day",
                (owner, s, e))}
        slots: dict[str, dict[str, dict]] = {}

        def slot(day: str, drill_id: str) -> dict:
            d = drills[drill_id]
            return slots.setdefault(day, {}).setdefault(drill_id, {
                "drill_id": drill_id, "title": d["title"], "unit": d["unit"], "target": d["target"],
                "plan_id": None, "routine_id": None})

        for plan_id, drill_id, day in plans:
            if drill_id in drills:
                slot(day, drill_id)["plan_id"] = plan_id
        for routine_id, drill_id, weekdays, r_start, r_end in routines:
            if drill_id not in drills:
                continue
            wd = {int(x) for x in weekdays.split(",")}
            for day in _days(max(start, datetime.date.fromisoformat(r_start)), min(end, datetime.date.fromisoformat(r_end))):
                if day.weekday() in wd and (routine_id, day.isoformat()) not in skips:
                    slot(day.isoformat(), drill_id)["routine_id"] = routine_id

        result = []
        for day in _days(start, end):
            key = day.isoformat()
            planned = []
            for item in slots.get(key, {}).values():
                count = logged.get((item["drill_id"], key), 0)
                status = "done" if count else "missed" if day < today else "today" if day == today else "upcoming"
                planned.append({**item, "count": count, "status": status})
            planned.sort(key=lambda p: p["title"])
            extra = [{"drill_id": drill_id, "title": drills[drill_id]["title"], "unit": drills[drill_id]["unit"], "count": n}
                     for (drill_id, d), n in logged.items() if d == key and drill_id in drills and drill_id not in slots.get(key, {})]
            result.append({"date": key, "is_today": day == today, "planned": planned, "extra": extra,
                           "done": sum(p["status"] == "done" for p in planned), "total": len(planned)})
        return result

    def calendar(self, owner: str, year: int, month: int, today: datetime.date | None = None) -> dict:
        """월 달력(월요일 시작 주들)과 이번 주·이번 달 달성률, 루틴 목록."""
        today = today or datetime.date.today()
        weeks = month_grid(year, month)
        days = self._schedule(owner, weeks[0][0], weeks[-1][-1], today)
        for d in days:
            d["in_month"] = d["date"][:7] == f"{year:04d}-{month:02d}"
        week_start, week_end = week_bounds(today)
        week_days = self._schedule(owner, week_start, week_end, today)
        month_days = [d for d in days if d["in_month"]]
        return {
            "year": year, "month": month, "today": today.isoformat(),
            "weeks": [days[i : i + 7] for i in range(0, len(days), 7)],
            "week_rate": _rate(week_days, today),
            "month_rate": _rate(month_days, today),
            "routines": self.routines(owner),
        }

    def day_plan(self, owner: str, day: datetime.date, today: datetime.date | None = None) -> dict:
        """하루 계획(오늘의 훈련 등)."""
        return self._schedule(owner, day, day, today or datetime.date.today())[0]

    # ── 판정 기반 추천 플랜: 문제 코드 → 2주 플랜 제안, 받으면 루틴 ──

    _PROPOSAL_COLUMNS = ("id", "owner", "issue_codes", "drill_keys", "weekdays", "start_day", "end_day", "status", "created_at")

    @staticmethod
    def _public_proposal(row: tuple) -> dict:
        r = dict(zip(TrainingStore._PROPOSAL_COLUMNS, row))
        weekdays = [int(x) for x in r["weekdays"].split(",")]
        start, end = datetime.date.fromisoformat(r["start_day"]), datetime.date.fromisoformat(r["end_day"])
        drills = [LIBRARY_BY_KEY[k] for k in r["drill_keys"].split(",") if k in LIBRARY_BY_KEY]
        return {
            "id": r["id"], "owner": r["owner"], "status": r["status"], "created_at": r["created_at"],
            "issue_codes": r["issue_codes"].split(","), "weekdays": weekdays,
            "start": r["start_day"], "end": r["end_day"],
            "sessions": sum(1 for d in _days(start, end) if d.weekday() in weekdays),
            "drills": [{k: d[k] for k in ("key", "title", "unit", "target", "minutes", "location", "doc_id")} for d in drills],
        }

    def _proposal_rows(self, where: str, args: tuple) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {', '.join(self._PROPOSAL_COLUMNS)} FROM training_proposals WHERE {where} ORDER BY created_at, rowid", args
            ).fetchall()
        return [self._public_proposal(r) for r in rows]

    def propose(self, owner: str, issue_codes: list[str], today: datetime.date | None = None) -> dict | None:
        """문제 코드에 맞는 라이브러리 항목으로 2주 플랜을 제안합니다(규칙, LLM 없음).

        같은 문제로 받을지 기다리는 제안이나 받아서 진행 중인 플랜이 있으면 그 문제는 빼고, 남는 게 없으면 None.
        """
        today = today or datetime.date.today()
        covered = {
            code
            for p in self._proposal_rows("owner = ? AND (status = 'pending' OR (status = 'accepted' AND end_day >= ?))",
                                         (owner, today.isoformat()))
            for code in p["issue_codes"]
        }
        codes = []
        for code in issue_codes:   # 판정에 나온 순서를 지키며 중복 제거
            if code not in codes and code not in covered and any(code in item["issue_codes"] for item in CATALOG):
                codes.append(code)
        if not codes:
            return None
        keys = [item["key"] for item in CATALOG if set(item["issue_codes"]) & set(codes)]
        row = (secrets.token_hex(8), owner, ",".join(codes), ",".join(keys), ",".join(map(str, PLAN_WEEKDAYS)),
               today.isoformat(), (today + datetime.timedelta(days=PLAN_DAYS - 1)).isoformat(), "pending", _now())
        with self._lock:
            self._conn.execute(
                f"INSERT INTO training_proposals ({', '.join(self._PROPOSAL_COLUMNS)}) VALUES ({', '.join('?' * len(row))})", row)
        return self._public_proposal(row)

    def proposals(self, owner: str) -> list[dict]:
        """받을지 기다리는 제안."""
        return self._proposal_rows("owner = ? AND status = 'pending'", (owner,))

    def proposal_owner(self, proposal_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT owner FROM training_proposals WHERE id = ?", (proposal_id,)).fetchone()
        return row[0] if row else None

    def accept_proposal(self, owner: str, proposal_id: str, today: datetime.date | None = None) -> dict:
        """제안을 받아 항목을 내 항목에 넣고(이미 있으면 그대로) 요일 루틴을 만듭니다. 늦게 받으면 오늘부터."""
        today = today or datetime.date.today()
        found = self._proposal_rows("owner = ? AND id = ?", (owner, proposal_id))
        if not found or found[0]["status"] != "pending":
            raise TrainingError("받을 수 없는 제안입니다.")
        proposal = found[0]
        start = max(datetime.date.fromisoformat(proposal["start"]), today)
        end = datetime.date.fromisoformat(proposal["end"])
        if end < start:
            raise TrainingError("기간이 지난 제안입니다. 다시 판정을 받아 새 플랜을 만들어 주세요.")
        routines = []
        active = {d["key"]: d for d in self.drills(owner) if d["key"]}
        for d in proposal["drills"]:
            drill = active.get(d["key"])
            if drill is None:   # 판정 추천으로 생긴 항목은 source='auto'
                item = LIBRARY_BY_KEY[d["key"]]
                code = next((c for c in proposal["issue_codes"] if c in item["issue_codes"]), None)
                drill = self._insert_from_library(owner, item, source="auto", issue_code=code)
            routines.append(self.add_routine(owner, drill["id"], proposal["weekdays"], start, end))
        with self._lock:
            self._conn.execute("UPDATE training_proposals SET status = 'accepted' WHERE id = ?", (proposal_id,))
        return {**proposal, "status": "accepted", "routines": routines}

    def dismiss_proposal(self, owner: str, proposal_id: str) -> bool:
        with self._lock:
            return self._conn.execute(
                "UPDATE training_proposals SET status = 'dismissed' WHERE owner = ? AND id = ? AND status = 'pending'",
                (owner, proposal_id),
            ).rowcount == 1

    def drill_owner(self, drill_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT owner FROM training_drills WHERE id = ?", (drill_id,)).fetchone()
        return row[0] if row else None

    def plan_owner(self, plan_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT owner FROM training_plans WHERE id = ?", (plan_id,)).fetchone()
        return row[0] if row else None

    def routine_owner(self, routine_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT owner FROM training_routines WHERE id = ?", (routine_id,)).fetchone()
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

    def delete_routine_any(self, routine_id: str) -> bool:
        owner = self.routine_owner(routine_id)
        return owner is not None and self.delete_routine(owner, routine_id)

    def overview(self, owner: str, today: datetime.date | None = None) -> dict:
        """[관리자 목록] 한 사용자의 훈련 요약: 항목 수, 이번 주 연습일·연속, 이번 주·이번 달 달성률, 마지막 연습일."""
        today = today or datetime.date.today()
        s = self.summary(owner, today)
        week_start, week_end = week_bounds(today)
        month_start = today.replace(day=1)
        month_end = today.replace(day=calendar.monthrange(today.year, today.month)[1])
        days = [d["last_day"] for d in s["drills"] if d["last_day"]]
        return {
            "username": owner, "drills": len(s["drills"]), "week_days": s["week_days"], "streak": s["streak"],
            "week_target_days": s["week_target_days"],
            "week_rate": _rate(self._schedule(owner, week_start, week_end, today), today),
            "month_rate": _rate(self._schedule(owner, month_start, month_end, today), today),
            "last_day": max(days) if days else None, "proposals": len(self.proposals(owner)),
        }
