"""POST /query 진입점 (FastAPI).

API 표준 규약
    요청:  {"question": "사용자 질의"}
    응답:  {"answer": ..., "contexts": [{"doc_id", "text"}], "trace": [{"step", "input", "output"}]}

이 서비스의 추가 필드 (표준 필드는 그대로 두고 덧붙입니다)
    요청:  image_base64(측면 어드레스 사진), video_base64(정면 스윙 영상), thread_id(대화 이어가기),
           model(코칭 모델 ID, GET /models 목록 중 하나) — 모두 선택
    응답:  status(answered|no_answer|blocked), issue_codes, sources, agents(부른 sub-agent), model(코칭에 쓴 모델)

계정 (아이디·비밀번호, 세션은 HttpOnly 쿠키)
    POST /auth/signup  {"username", "password"}  가입하고 바로 로그인
    POST /auth/login   {"username", "password"}  로그인
    POST /auth/logout                            로그아웃
    GET  /auth/me                                {"username": 아이디 또는 null}
    GET  /auth/avatar.svg                        내 캐릭터 아이콘(가입할 때 무작위로 정함, src/avatar.py)
    관리자 'admin' 은 .env 의 ADMIN_PASSWORD 로 서버가 만듭니다(화면 가입 불가). 관리자 전용(아니면 403):
    GET /admin/threads · DELETE /admin/threads/{thread_id} · GET /admin/avatars/{username}.svg
    질문·사진·영상 판정은 로그인 없이(게스트) 쓸 수 있습니다. 장기 기억은 로그인한 본인만 쓰며,
    주인은 요청 본문이 아니라 세션 쿠키로 정합니다(남의 기록을 읽거나 지울 수 없게).

장기 기억 (로그인 계정별 자세 판정 기록, 대화방을 넘어 유지) — 로그인하지 않으면 401
    GET    /memory  내 판정 기록 보기
    DELETE /memory  모두 지우기

코칭 내역 (로그인 계정별 대화 목록, 화면의 코칭 카드) — 로그인하지 않으면 401
    GET    /threads              내 코칭 내역
    PUT    /threads/{thread_id}  하나 저장(화면이 만든 JSON 그대로)
    DELETE /threads/{thread_id}  하나 지우기 · DELETE /threads 모두 지우기
    대화방(thread_id)은 처음 쓴 계정이 주인입니다. 다른 계정·게스트가 그 thread_id 로 /query 를 보내면 403.

입력 검사
    - 모르는 필드(예: 채점용 pose_fixture)가 오면 422 로 거부합니다. fixture 는 함수 주입으로만 받습니다.
    - base64 를 풀 수 없으면 422, 디코딩 후 10MB 를 넘으면 413 입니다.
    - 사진과 영상을 동시에 보내면 422 입니다.
    - model 이 허용 목록(config.AVAILABLE_MODELS)에 없으면 422 입니다.

실행 모드는 환경변수 GOLF_MODE 로 고릅니다. live(기본, 실제 Bedrock·Chroma·MediaPipe) | offline(가짜 모델·BM25)
실행: uvicorn src.app:app --port 8000   (miniPJT 폴더에서)
"""

from __future__ import annotations

import base64
import binascii
import csv
import json
import os
import threading
from functools import lru_cache

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src import config
from src.agent import GolfCoachAgent, build_agent
from src.auth import AccountStore, AuthError, is_admin, memory_owner
from src.history import THREAD_ID_PATTERN, HistoryStore, ThreadOwnedByOther


def _allowed_model(value: str | None) -> str | None:
    if value is not None and value not in config.AVAILABLE_MODEL_IDS:
        raise ValueError("사용할 수 없는 모델입니다. GET /models 목록에서 골라 주세요.")
    return value


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    question: str = Field(min_length=1, description="사용자 질의")
    image_base64: str | None = Field(default=None, description="측면(down-the-line) 어드레스 사진")
    video_base64: str | None = Field(default=None, description="정면(face-on) 스윙 영상 짧은 클립 (mp4·webm·mov, 움직이는 GIF 도 가능)")
    thread_id: str | None = Field(default=None, description="같은 값을 보내면 대화가 이어집니다")
    model: str | None = Field(default=None, description="코칭 모델 ID. 없으면 기본 모델(Claude Sonnet 4.5)")

    @field_validator("model")
    @classmethod
    def check_model(cls, value: str | None) -> str | None:
        return _allowed_model(value)


class ContextItem(BaseModel):
    doc_id: str
    text: str


class TraceItem(BaseModel):
    step: str
    input: str
    output: str


class QueryResponse(BaseModel):
    answer: str
    contexts: list[ContextItem]
    trace: list[TraceItem]
    status: str
    issue_codes: list[str]
    sources: list[str]
    agents: list[str] = Field(default_factory=list, description="이번 질문에 supervisor 가 부른 sub-agent (순서대로)")
    angle_series: dict | None = Field(
        default=None,
        description="영상 판정일 때 프레임별 왼팔 각도 {metric, t(초 또는 null), values, apex_index, threshold}",
    )
    model: str


app = FastAPI(title="SwingKid · 골프 입문 코치", version="0.1.0")


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass


@lru_cache(maxsize=1)
def _cached_agent() -> GolfCoachAgent:
    _load_env()
    return build_agent(os.getenv("GOLF_MODE", "live"))


_AGENT_LOCK = threading.Lock()


def get_agent() -> GolfCoachAgent:
    """에이전트는 처음 요청이 올 때 한 번만 만듭니다(모델을 모듈 최상단에서 만들지 않는 규약).

    잠금이 필요한 이유: 화면을 처음 열면 요청 여러 개가 동시에 들어옵니다. lru_cache 만으로는 동시에 들어온
    첫 호출들이 각자 에이전트를 만들어, 그 사이에 온 판정이 버려지는 에이전트의 장기 기억에 저장될 수 있었습니다.
    """
    with _AGENT_LOCK:
        return _cached_agent()


get_agent.cache_clear = _cached_agent.cache_clear   # 테스트에서 모드를 바꿀 때 씀


def _check_media(value: str | None, label: str) -> None:
    if value is None:
        return
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail=f"{label}를 base64로 풀 수 없습니다. 파일을 base64 문자열로 보내 주세요.")
    if len(raw) > config.MAX_MEDIA_BYTES:
        raise HTTPException(status_code=413, detail=f"{label}가 10MB를 넘습니다. 짧은 클립이나 작은 사진으로 보내 주세요.")


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest, request: Request) -> dict:
    if req.image_base64 and req.video_base64:
        raise HTTPException(status_code=422, detail="사진과 영상은 한 번에 하나만 보내 주세요.")
    _check_media(req.image_base64, "사진")
    _check_media(req.video_base64, "영상")
    _check_thread(request, req.thread_id)
    try:
        return get_agent().ask(
            req.question,
            image_base64=req.image_base64,
            video_base64=req.video_base64,
            thread_id=req.thread_id,
            model=req.model,
            user_id=_memory_owner(request),
        )
    except ModuleNotFoundError as exc:
        # 사진·영상 분석에 필요한 MediaPipe·OpenCV 가 없는 환경(예: Python 3.14, 오프라인 모드)
        raise HTTPException(
            status_code=503,
            detail=f"사진·영상 분석 도구({exc.name})가 설치되어 있지 않습니다. 텍스트 질문은 그대로 쓸 수 있고, "
            "사진·영상은 Python 3.12 환경에 mediapipe·opencv-python 을 설치한 라이브 모드에서 분석합니다.",
        )
    except Exception as exc:
        raise _bedrock_http_error(exc) from exc


def _bedrock_http_error(exc: Exception) -> Exception:
    """Bedrock(botocore) 오류를 사용자가 읽을 수 있는 HTTP 오류로 바꿉니다. 그 밖의 오류는 그대로 돌려줍니다."""
    code = (getattr(exc, "response", None) or {}).get("Error", {}).get("Code") if hasattr(exc, "response") else None
    if code in {"ThrottlingException", "ServiceQuotaExceededException", "TooManyRequestsException"}:
        return HTTPException(
            status_code=429,
            detail="Bedrock 사용량 한도에 걸렸습니다(일일 토큰 또는 분당 요청 수). "
            "잠시 뒤 다시 시도하거나 상단에서 다른 코칭 모델을 골라 주세요.",
        )
    if code:
        return HTTPException(status_code=502, detail=f"Bedrock 호출에 실패했습니다: {code}")
    return exc


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "mode": _mode()}


def _mode() -> str:
    return os.getenv("GOLF_MODE", "live")


def _demo_enabled() -> bool:
    """fixture 데모 경로: 오프라인은 항상, 라이브는 GOLF_DEMO=1 일 때만 엽니다."""
    return _mode() == "offline" or os.getenv("GOLF_DEMO") == "1"


@app.get("/models")
def models() -> dict:
    """화면의 모델 선택 상자용 목록. 오프라인 모드에서는 고른 모델과 상관없이 가짜 모델이 답합니다."""
    return {"mode": _mode(), "default": config.MODEL_ID, "models": config.AVAILABLE_MODELS}


# ── 테스트 웹 페이지와 데모 전용 경로 ───────────────────────
# /query 의 규약은 바꾸지 않습니다. 아래 /demo/* 는 오프라인 모드에서 화면으로 시험해 보기 위한 보조 경로입니다.

STATIC_DIR = config.PROJECT_ROOT / "src" / "static"


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/architecture", include_in_schema=False)
def architecture() -> FileResponse:
    """멀티 agent 아키텍처 페이지(크게 보기). 다이어그램은 /static/arch.js 가 그리며, 메인 화면 아래쪽에도 같은 것이 들어갑니다."""
    return FileResponse(STATIC_DIR / "architecture.html")


@app.get("/static/arch.js", include_in_schema=False)
def architecture_script() -> FileResponse:
    return FileResponse(STATIC_DIR / "arch.js", media_type="text/javascript")


@app.get("/architecture/code/{module}")
def architecture_code(module: str) -> dict:
    """다이어그램에서 누른 모듈의 중요 코드(실제 소스 파일에서 함수 단위로 잘라 줄 번호와 함께)."""
    from src.arch_code import MODULES, code_for

    if module not in MODULES:
        raise HTTPException(status_code=404, detail="알 수 없는 모듈입니다.")
    return code_for(module)


# ── 사진·영상 샘플 (data/test_photos, data/test_videos) ──────
# 화면의 '사진·영상 샘플' 목록. 고르면 화면이 파일을 받아 직접 올린 것과 똑같이 /query 로 보냅니다(실제 포즈 분석).
# 출처·라이선스는 각 폴더의 SOURCES.md 에 있습니다.

SAMPLE_DIRS = (config.PROJECT_ROOT / "data" / "test_photos", config.PROJECT_ROOT / "data" / "test_videos")
SAMPLE_TYPES = {
    ".jpg": ("photo", "image/jpeg"), ".jpeg": ("photo", "image/jpeg"), ".png": ("photo", "image/png"),
    ".webm": ("video", "video/webm"), ".mp4": ("video", "video/mp4"), ".mov": ("video", "video/quicktime"),
    ".gif": ("video", "image/gif"),
}


def _sample_label(path) -> str:
    import re

    stem = path.stem
    if m := re.fullmatch(r"side_address_(\d+)", stem):
        return f"측면 어드레스 사진 {int(m.group(1))}"
    if m := re.fullmatch(r"face_on_swing_(\d+)", stem):
        return f"정면 스윙 영상 {int(m.group(1))}"
    if stem == "too_small_people":
        return "사람이 작게 찍힌 사진 (인식 실패 예시)"
    if path.suffix.lower() == ".gif":
        return "정면 스윙 GIF"
    return path.name


def _media_samples() -> dict:
    """파일 이름 → 경로·종류. 목록에 있는 파일만 내려 주므로 다른 경로를 열 수 없습니다."""
    found = {}
    for folder in SAMPLE_DIRS:
        for path in sorted(folder.glob("*")):
            if path.is_file() and path.suffix.lower() in SAMPLE_TYPES:
                found[path.name] = path
    return found


@app.get("/samples/media")
def media_samples() -> dict:
    """화면의 '사진·영상 샘플' 목록(사진 먼저, 그다음 영상)."""
    items = []
    for name, path in _media_samples().items():
        kind, mime = SAMPLE_TYPES[path.suffix.lower()]
        items.append({"name": name, "kind": kind, "type": mime, "label": _sample_label(path),
                      "size": path.stat().st_size, "url": f"/samples/media/{name}"})
    items.sort(key=lambda s: (s["kind"] != "photo", "예시" in s["label"], s["label"]))   # 사진 먼저, 실패 예시는 맨 뒤
    gif_count = 0
    for s in items:   # 이름이 해시인 GIF 가 여러 개면 번호를 붙입니다
        if s["type"] == "image/gif":
            gif_count += 1
            s["label"] = f"{s['label']} {gif_count}"
    return {"samples": items}


@app.get("/samples/media/{name}", include_in_schema=False)
def media_sample_file(name: str) -> FileResponse:
    path = _media_samples().get(name)
    if path is None:
        raise HTTPException(status_code=404, detail="샘플을 찾을 수 없습니다.")
    return FileResponse(path, media_type=SAMPLE_TYPES[path.suffix.lower()][1])


@app.get("/demo/samples")
def demo_samples() -> dict:
    """화면의 예시 버튼에 쓸 test_queries.csv 질문과 포즈 fixture 목록."""
    rows = list(csv.DictReader((config.PROJECT_ROOT / "evaluation" / "test_queries.csv").open(encoding="utf-8-sig")))
    # 관절 좌표 fixture 만 (같은 폴더의 web_search.json 은 가짜 웹 검색 결과라 제외)
    fixtures = sorted(p.stem for p in config.FIXTURES_DIR.glob("*.json") if p.stem.startswith(("photo_", "video_")))
    return {
        "mode": _mode(),
        "demo": _demo_enabled(),
        "queries": [{k: r[k] for k in ("id", "category", "input")} for r in rows],
        "fixtures": fixtures if _demo_enabled() else [],
    }


class DemoFixtureRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    question: str = Field(min_length=1)
    fixture: str = Field(pattern=r"^[a-z_]+$")
    thread_id: str | None = None
    model: str | None = None

    @field_validator("model")
    @classmethod
    def check_model(cls, value: str | None) -> str | None:
        return _allowed_model(value)


@app.post("/demo/fixture", response_model=QueryResponse)
def demo_fixture(req: DemoFixtureRequest, request: Request) -> dict:
    """[데모 전용] 저장된 더미 관절 좌표를 주입해 사진·영상 판정을 시험합니다.

    오프라인 모드에서는 항상, 라이브 모드에서는 GOLF_DEMO=1 로 띄웠을 때만 열립니다.
    서버에 있는 fixture 파일 이름만 받으므로 임의의 관절 좌표를 넣는 입구가 되지 않습니다.
    """
    if not _demo_enabled():
        raise HTTPException(status_code=404, detail="데모 경로는 오프라인 모드나 GOLF_DEMO=1 에서만 열립니다.")
    path = config.FIXTURES_DIR / f"{req.fixture}.json"
    if not req.fixture.startswith(("photo_", "video_")) or not path.exists():
        raise HTTPException(status_code=404, detail=f"fixture 를 찾을 수 없습니다: {req.fixture}")
    fixture = json.loads(path.read_text(encoding="utf-8"))
    _check_thread(request, req.thread_id)
    try:
        return get_agent().ask(
            req.question, thread_id=req.thread_id, pose_fixture=fixture, model=req.model, user_id=_memory_owner(request)
        )
    except Exception as exc:
        raise _bedrock_http_error(exc) from exc


# ── 계정: 가입·로그인·로그아웃 ───────────────────────────

SESSION_COOKIE = "swingkid_session"


@lru_cache(maxsize=2)
def _accounts_for(mode: str) -> AccountStore:
    store = AccountStore(config.ACCOUNTS_DB if mode == "live" else ":memory:")
    _load_env()
    admin_password = os.getenv("ADMIN_PASSWORD")
    if admin_password:   # 관리자 계정은 .env 가 기준(없으면 관리자 없이 동작)
        try:
            store.ensure_admin(admin_password)
        except AuthError as exc:
            print(f"[경고] ADMIN_PASSWORD 를 쓸 수 없어 관리자 계정을 만들지 않았습니다: {exc}")
    return store


@lru_cache(maxsize=2)
def _history_for(mode: str) -> HistoryStore:
    return HistoryStore(config.ACCOUNTS_DB if mode == "live" else ":memory:")


def get_accounts() -> AccountStore:
    """오프라인은 메모리 DB, 라이브는 config.ACCOUNTS_DB 파일."""
    return _accounts_for(_mode())


def get_history() -> HistoryStore:
    return _history_for(_mode())


def _clear_account_caches() -> None:   # 테스트에서 계정·코칭 내역을 비울 때 씀
    _accounts_for.cache_clear()
    _history_for.cache_clear()


get_accounts.cache_clear = _clear_account_caches


def _current_user(request: Request) -> str | None:
    return get_accounts().user_for(request.cookies.get(SESSION_COOKIE))


def _memory_owner(request: Request) -> str | None:
    """장기 기억 주인. 로그인하지 않은 게스트는 None(저장·조회 안 함)."""
    username = _current_user(request)
    return memory_owner(username) if username else None


def _check_thread(request: Request, thread_id: str | None) -> None:
    """남의 대화방을 이어 쓰지 못하게 합니다. 로그인했으면 처음 쓰는 대화방의 주인이 됩니다."""
    if not thread_id:
        return
    username = _current_user(request)
    history = get_history()
    try:
        if username:
            history.claim(thread_id, username)
        elif history.owner_of(thread_id) is not None:
            raise ThreadOwnedByOther(thread_id)
    except ThreadOwnedByOther:
        raise HTTPException(status_code=403, detail="다른 계정의 코칭입니다. 새 코칭으로 시작해 주세요.")


def _require_user(request: Request) -> str:
    username = _current_user(request)
    if username is None:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    return username


def _is_https(request: Request) -> bool:
    """Cloudflare Worker·터널 뒤에서는 백엔드가 http 로 받아도 브라우저는 https 입니다."""
    return (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto", "").lower() == "https"
        or '"https"' in request.headers.get("cf-visitor", "")
    )


def _set_session(response: Response, request: Request, token: str) -> None:
    from src.auth import SESSION_DAYS

    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
        secure=_is_https(request), path="/",
    )


class AuthRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


@app.post("/auth/signup")
def auth_signup(req: AuthRequest, request: Request, response: Response) -> dict:
    """가입하고 바로 로그인합니다. 아이디가 이미 있으면 409, 규칙에 안 맞으면 422."""
    accounts = get_accounts()
    try:
        accounts.signup(req.username, req.password)
    except AuthError as exc:
        status = 409 if "이미" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc))
    _set_session(response, request, accounts.login(req.username, req.password))
    return {"username": req.username, "admin": False}


@app.post("/auth/login")
def auth_login(req: AuthRequest, request: Request, response: Response) -> dict:
    try:
        token = get_accounts().login(req.username, req.password)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    _set_session(response, request, token)
    return {"username": req.username, "admin": is_admin(req.username)}


@app.post("/auth/logout")
def auth_logout(request: Request, response: Response) -> dict:
    get_accounts().logout(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"username": None}


@app.get("/auth/avatar.svg", include_in_schema=False)
def auth_avatar(request: Request) -> Response:
    """로그인한 본인의 캐릭터 아이콘. 고정 부품으로만 그린 SVG 입니다."""
    from src.avatar import render_svg

    username = _require_user(request)
    svg = render_svg(get_accounts().avatar(username))
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "private, max-age=86400"})


@app.get("/auth/me")
def auth_me(request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store"
    username = _current_user(request)
    return {"username": username, "admin": is_admin(username)}


# ── 장기 기억 보기·지우기 (로그인한 본인 것만) ─────────────


@app.get("/memory")
def memory_list(request: Request, response: Response) -> dict:
    """내 장기 기억(자세 판정 기록, 최신순). 판정마다 바뀌므로 브라우저가 캐시하지 않게 합니다."""
    username = _require_user(request)
    response.headers["Cache-Control"] = "no-store"
    records = get_agent().memory.findings(memory_owner(username), limit=100)
    return {"username": username, "count": len(records), "findings": records}


@app.delete("/memory")
def memory_clear(request: Request) -> dict:
    """내 장기 기억을 모두 지웁니다(대화방 기록은 그대로)."""
    username = _require_user(request)
    return {"username": username, "deleted": get_agent().memory.clear(memory_owner(username))}


# ── 코칭 내역 (로그인한 본인 것만) ─────────────────────────


def _check_thread_id(thread_id: str) -> None:
    import re

    if not re.fullmatch(THREAD_ID_PATTERN, thread_id):
        raise HTTPException(status_code=422, detail="코칭 id 는 영문·숫자·-·_ 1~64자입니다.")


@app.get("/threads")
def threads_list(request: Request, response: Response) -> dict:
    username = _require_user(request)
    response.headers["Cache-Control"] = "no-store"
    return {"username": username, "threads": get_history().list(username)}


@app.put("/threads/{thread_id}")
async def threads_put(thread_id: str, request: Request) -> dict:
    """코칭 하나를 저장합니다. 본문은 화면의 코칭 JSON {id, created, turns}, 최대 config.MAX_THREAD_BYTES."""
    username = _require_user(request)
    _check_thread_id(thread_id)
    raw = await request.body()
    if len(raw) > config.MAX_THREAD_BYTES:
        raise HTTPException(status_code=413, detail="코칭 기록이 너무 큽니다.")
    try:
        data = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=422, detail="JSON 이 아닙니다.")
    if not isinstance(data, dict) or data.get("id") != thread_id or not isinstance(data.get("turns"), list):
        raise HTTPException(status_code=422, detail="코칭 JSON 은 {id, created, turns:[...]} 이고 id 가 주소와 같아야 합니다.")
    try:
        get_history().put(username, thread_id, data)
    except ThreadOwnedByOther:
        raise HTTPException(status_code=403, detail="다른 계정의 코칭입니다.")
    return {"id": thread_id, "saved": True}


@app.delete("/threads/{thread_id}")
def threads_delete(thread_id: str, request: Request) -> dict:
    username = _require_user(request)
    _check_thread_id(thread_id)
    return {"id": thread_id, "deleted": get_history().delete(username, thread_id)}


@app.delete("/threads")
def threads_clear(request: Request) -> dict:
    username = _require_user(request)
    return {"deleted": get_history().clear(username)}


# ── 관리자: 모든 계정의 코칭 보기·지우기 (이어 쓰기는 _check_thread 가 막음) ──


def _require_admin(request: Request) -> str:
    username = _require_user(request)
    if not is_admin(username):
        raise HTTPException(status_code=403, detail="관리자만 쓸 수 있습니다.")
    return username


@app.get("/admin/threads")
def admin_threads(request: Request, response: Response) -> dict:
    """다른 모든 계정의 코칭 [{owner, thread}] (관리자 자신의 코칭은 GET /threads)."""
    admin = _require_admin(request)
    response.headers["Cache-Control"] = "no-store"
    return {"threads": get_history().list_all(exclude_owner=admin)}


@app.delete("/admin/threads/{thread_id}")
def admin_delete_thread(thread_id: str, request: Request) -> dict:
    _require_admin(request)
    _check_thread_id(thread_id)
    return {"id": thread_id, "deleted": get_history().delete_any(thread_id)}


@app.get("/admin/avatars/{username}.svg", include_in_schema=False)
def admin_avatar(username: str, request: Request) -> Response:
    from src.avatar import render_svg

    _require_admin(request)
    avatar = get_accounts().avatar(username)
    if avatar is None:
        raise HTTPException(status_code=404, detail="없는 계정입니다.")
    return Response(render_svg(avatar), media_type="image/svg+xml", headers={"Cache-Control": "private, max-age=86400"})

