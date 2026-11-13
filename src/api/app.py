"""POST /query 진입점 (FastAPI).

API 표준 규약
    요청:  {"question": "사용자 질의"}
    응답:  {"answer": ..., "contexts": [{"doc_id", "text"}], "trace": [{"step", "input", "output"}]}

이 서비스의 추가 필드 (표준 필드는 그대로 두고 덧붙입니다)
    요청:  image_base64(측면 어드레스 사진), video_base64(정면 스윙 영상), thread_id(대화 이어가기),
           model(코칭 모델 ID, GET /models 목록 중 하나) — 모두 선택
    응답:  status(answered|no_answer|blocked), issue_codes, sources, agents(부른 sub-agent), model(코칭에 쓴 모델),
           training_proposal(이번 판정으로 만든 2주 연습 플랜 제안, 로그인했을 때만)

계정 (아이디·비밀번호, 세션은 HttpOnly 쿠키)
    POST /auth/signup  {"username", "password"}  가입하고 바로 로그인
    POST /auth/login   {"username", "password"}  로그인
    POST /auth/logout                            로그아웃
    GET  /auth/me                                {"username": 아이디 또는 null}
    GET  /auth/avatar.svg                        내 캐릭터 아이콘(가입할 때 무작위로 정함, src/accounts/avatar.py)
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

훈련 관리 (SERVICE.md 6장, src/training/service.py) — 라이브러리 조회 말고는 로그인 필요(401), 남의 것은 403, 없으면 404
    GET    /training/library                 훈련 라이브러리(분야·항목). 게스트도 볼 수 있음
    GET    /training                         내 항목과 집계, 최근 기록, 오늘 계획(today_plan), 받을지 기다리는 제안(proposals)
    POST   /training/drills                  {"title", "unit": 회|분|개, "target"} 직접 추가
    POST   /training/drills/from-library     {"key"} 라이브러리 항목을 내 항목에(이미 있으면 그대로)
    DELETE /training/drills/{id}             목록에서 내리기(기록은 남김, 일정에서도 빠짐)
    POST   /training/drills/{id}/logs        {"count", "day"?: YYYY-MM-DD(없으면 오늘), "memo"?} 기록
    DELETE /training/logs/{id}               기록 지우기
    POST   /training/plans                   {"drill_id", "day"} 날짜에 배치 · DELETE /training/plans/{id}
    POST   /training/routines                {"drill_id", "weekdays": [0=월..6=일], "start", "end"} 요일 루틴
    DELETE /training/routines/{id}           루틴 지우기 · POST /training/routines/{id}/skip {"day"} 하루 빼기
    GET    /training/drills/{id}/trend       판정과 연결된 항목의 각도 변화(내 판정 기록)와 기준 구간
    GET    /training/calendar?year=&month=   월 달력(날마다 완료·오늘·지남·예정)과 이번 주·이번 달 달성률
    POST   /training/proposals/{id}/accept   추천 플랜 받기(루틴 생성) · POST /training/proposals/{id}/dismiss 닫기
    사진·영상 판정에서 문제 코드가 나오면 규칙으로 2주 플랜(월·수·금)을 제안합니다(응답 training_proposal).
    관리자: GET /admin/training · DELETE /admin/training/drills/{id} · DELETE /admin/training/logs/{id}

입력 검사
    - 모르는 필드(예: 채점용 pose_fixture)가 오면 422 로 거부합니다. fixture 는 함수 주입으로만 받습니다.
    - base64 를 풀 수 없으면 422, 디코딩 후 10MB 를 넘으면 413 입니다.
    - 사진과 영상을 동시에 보내면 422 입니다.
    - model 이 허용 목록(config.AVAILABLE_MODELS)에 없으면 422 입니다.

실행 모드는 환경변수 GOLF_MODE 로 고릅니다. live(기본, 실제 Bedrock·Chroma·MediaPipe) | offline(가짜 모델·BM25)
실행: uvicorn src.api.app:app --port 8000   (miniPJT 폴더에서)
"""

from __future__ import annotations

import base64
import binascii
import csv
import datetime
import hmac
import json
import os
import secrets
import threading
from functools import lru_cache

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse

from src.accounts.auth import AccountStore, AuthError, is_admin, memory_owner
from src.accounts.google import CALLBACK_PATH, GoogleOAuth
from src.accounts.schemas import AuthRequest
from src.coach.graph import GolfCoachAgent, build_agent
from src.coach.schemas import DemoFixtureRequest, QueryRequest, QueryResponse
from src.history.store import THREAD_ID_PATTERN, HistoryStore, ThreadOwnedByOther
from src.shared import config
from src.training.schemas import (
    DrillCreateRequest,
    FromLibraryRequest,
    LogCreateRequest,
    PlanCreateRequest,
    RoutineCreateRequest,
    SkipRequest,
)
from src.training.service import TrainingError, TrainingStore

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
        result = get_agent().ask(
            req.question,
            image_base64=req.image_base64,
            video_base64=req.video_base64,
            thread_id=req.thread_id,
            model=req.model,
            user_id=_memory_owner(request),
            practice=_practice_for(request),
        )
        return _with_training(request, result)
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

STATIC_DIR = config.PROJECT_ROOT / "src" / "api" / "static"


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


# 웹앱 설치(PWA) 파일. Cloudflare 배포에서는 Worker 가 src/api/static 을 직접 내려주고, 여기는 로컬 실행용입니다.
@app.get("/manifest.webmanifest", include_in_schema=False)
def web_manifest() -> FileResponse:
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
def service_worker() -> FileResponse:
    return FileResponse(STATIC_DIR / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})


APP_ICONS = {p.name: p for p in (STATIC_DIR / "icons").glob("*") if p.suffix in {".png", ".svg"}}


@app.get("/icons/{name}", include_in_schema=False)
def app_icon(name: str) -> FileResponse:
    path = APP_ICONS.get(name)
    if path is None:
        raise HTTPException(status_code=404, detail="없는 아이콘입니다.")
    return FileResponse(path)


@app.get("/architecture/code/{module}")
def architecture_code(module: str) -> dict:
    """다이어그램에서 누른 모듈의 중요 코드(실제 소스 파일에서 함수 단위로 잘라 줄 번호와 함께)."""
    from src.api.arch_code import MODULES, code_for

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
        result = get_agent().ask(
            req.question, thread_id=req.thread_id, pose_fixture=fixture, model=req.model, user_id=_memory_owner(request),
            practice=_practice_for(request),
        )
        return _with_training(request, result)
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


@lru_cache(maxsize=2)
def _training_for(mode: str) -> TrainingStore:
    return TrainingStore(config.ACCOUNTS_DB if mode == "live" else ":memory:")


def get_training() -> TrainingStore:
    return _training_for(_mode())


def _clear_account_caches() -> None:   # 테스트에서 계정·코칭 내역·훈련을 비울 때 씀
    _accounts_for.cache_clear()
    _history_for.cache_clear()
    _training_for.cache_clear()


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
    from src.accounts.auth import SESSION_DAYS

    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
        secure=_is_https(request), path="/",
    )


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


# ── 계정: Google 로그인 (src/accounts/google.py) ─────────────

GOOGLE_STATE_COOKIE = "swingkid_google_state"
GOOGLE_STATE_PATH = "/auth/google"
GOOGLE_STATE_SECONDS = 600


def get_google() -> GoogleOAuth | None:
    """.env 에 GOOGLE_CLIENT_ID·GOOGLE_CLIENT_SECRET 이 있을 때만 Google 로그인을 켭니다."""
    _load_env()
    return GoogleOAuth.from_env()


def _google_redirect_uri(request: Request, google: GoogleOAuth) -> str:
    """Google 에 등록한 콜백 주소. .env 의 GOOGLE_REDIRECT_URI 가 우선이고, 없으면 브라우저가 보고 있는 주소로 만듭니다.

    Cloudflare Worker 뒤에서는 Host 가 터널 주소로 바뀌므로 Worker 가 넘기는 X-Forwarded-Host 를 먼저 봅니다.
    이 값을 누가 바꿔 보내도 Google 은 등록한 주소가 아니면 거절하므로 다른 곳으로 코드가 새지 않습니다.
    """
    if google.redirect_uri:
        return google.redirect_uri
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{'https' if _is_https(request) else 'http'}://{host}{CALLBACK_PATH}"


@app.get("/auth/providers")
def auth_providers() -> dict:
    """화면이 보여 줄 로그인 방법. Google 은 .env 설정이 있을 때만 True."""
    return {"password": True, "google": get_google() is not None}


@app.get("/auth/google/login", include_in_schema=False)
def auth_google_login(request: Request) -> RedirectResponse:
    """Google 동의 화면으로 보냅니다. state·nonce 는 짧게 사는 HttpOnly 쿠키에 두고 콜백에서 맞춰 봅니다."""
    google = get_google()
    if google is None:
        raise HTTPException(status_code=404, detail="Google 로그인이 설정되지 않았습니다.")
    state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    response = RedirectResponse(google.auth_url(_google_redirect_uri(request, google), state, nonce), status_code=302)
    response.set_cookie(
        GOOGLE_STATE_COOKIE, f"{state}.{nonce}", max_age=GOOGLE_STATE_SECONDS, httponly=True, samesite="lax",
        secure=_is_https(request), path=GOOGLE_STATE_PATH,
    )
    return response


@app.get(CALLBACK_PATH, include_in_schema=False)
def auth_google_callback(
    request: Request, code: str | None = None, state: str | None = None, error: str | None = None
) -> RedirectResponse:
    """Google 이 돌려보내는 곳. 처음이면 계정을 만들고, 세션 쿠키를 심은 뒤 첫 화면으로 보냅니다.

    실패하면 이유를 화면에 알릴 수 있게 /?login_error=google_cancel | google 로 보냅니다.
    """
    def finish(query: str = "") -> RedirectResponse:
        response = RedirectResponse("/" + query, status_code=302)
        response.delete_cookie(GOOGLE_STATE_COOKIE, path=GOOGLE_STATE_PATH)
        return response

    google = get_google()
    if error == "access_denied":
        return finish("?login_error=google_cancel")
    saved_state, _, nonce = request.cookies.get(GOOGLE_STATE_COOKIE, "").partition(".")
    if google is None or error or not code or not state or not saved_state or not hmac.compare_digest(state, saved_state):
        return finish("?login_error=google")
    try:
        identity = google.exchange(code, _google_redirect_uri(request, google), nonce)
        token, _, _ = get_accounts().google_login(identity.sub, identity.email)
    except AuthError:
        return finish("?login_error=google")
    response = finish()
    _set_session(response, request, token)
    return response


@app.get("/auth/avatar.svg", include_in_schema=False)
def auth_avatar(request: Request) -> Response:
    """로그인한 본인의 캐릭터 아이콘. 고정 부품으로만 그린 SVG 입니다."""
    from src.accounts.avatar import render_svg

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
    from src.accounts.avatar import render_svg

    _require_admin(request)
    avatar = get_accounts().avatar(username)
    if avatar is None:
        raise HTTPException(status_code=404, detail="없는 계정입니다.")
    return Response(render_svg(avatar), media_type="image/svg+xml", headers={"Cache-Control": "private, max-age=86400"})


# ── 훈련 관리 (SERVICE.md 6장, src/training/service.py) ───────────────
# 로그인한 본인 것만. 남의 것은 403, 없는 것은 404. 판정에서 문제 코드가 나오면 2주 플랜을 규칙으로 제안합니다.


def _with_training(request: Request, result: dict) -> dict:
    """로그인한 사용자의 사진·영상 판정이면 문제 코드에 맞는 2주 연습 플랜을 제안하고 응답에 붙입니다."""
    username = _current_user(request)
    if not username or result.get("status") != "answered":
        return result
    if not any(step.get("step") == "pose" for step in result.get("trace", [])):
        return result
    proposal = get_training().propose(username, result.get("issue_codes", []))
    return {**result, "training_proposal": proposal}


def _practice_for(request: Request) -> str | None:
    """코칭 근거로 넘길 연습 요약. 게스트는 None, 로그인했지만 기록이 없으면 ''."""
    from src.training.service import practice_summary

    username = _current_user(request)
    return practice_summary(get_training(), username) if username else None


def _check_training_owner(owner: str | None, username: str, what: str) -> None:
    if owner is None:
        raise HTTPException(status_code=404, detail=f"없는 {what}입니다.")
    if owner != username:
        raise HTTPException(status_code=403, detail=f"다른 계정의 {what}입니다.")


def _training_view(username: str) -> dict:
    store = get_training()
    return {
        "username": username, **store.summary(username), "logs": store.logs(username, limit=100),
        "today_plan": store.day_plan(username, datetime.date.today()), "proposals": store.proposals(username),
    }


@app.get("/training")
def training_get(request: Request, response: Response) -> dict:
    """내 훈련: 진행 중 항목과 집계(이번 주 연습일·연속 연습일·누적), 최근 기록."""
    username = _require_user(request)
    response.headers["Cache-Control"] = "no-store"
    return _training_view(username)


@app.post("/training/drills")
def training_add_drill(req: DrillCreateRequest, request: Request) -> dict:
    username = _require_user(request)
    try:
        return get_training().add_custom(username, req.title, req.unit, req.target)
    except TrainingError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.delete("/training/drills/{drill_id}")
def training_archive_drill(drill_id: str, request: Request) -> dict:
    """항목을 목록에서 내립니다(기록은 남김)."""
    username = _require_user(request)
    store = get_training()
    _check_training_owner(store.drill_owner(drill_id), username, "훈련 항목")
    return {"id": drill_id, "archived": store.archive(username, drill_id)}


@app.post("/training/drills/{drill_id}/logs")
def training_add_log(drill_id: str, req: LogCreateRequest, request: Request) -> dict:
    username = _require_user(request)
    store = get_training()
    _check_training_owner(store.drill_owner(drill_id), username, "훈련 항목")
    try:
        return store.add_log(username, drill_id, req.day or datetime.date.today(), req.count, req.memo)
    except TrainingError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.delete("/training/logs/{log_id}")
def training_delete_log(log_id: str, request: Request) -> dict:
    username = _require_user(request)
    store = get_training()
    _check_training_owner(store.log_owner(log_id), username, "훈련 기록")
    return {"id": log_id, "deleted": store.delete_log(username, log_id)}


@app.get("/training/drills/{drill_id}/trend")
def training_trend(drill_id: str, request: Request, response: Response) -> dict:
    """판정과 연결된 항목의 각도 변화(내 장기 기억 판정 기록, 오래된 순)와 교육용 기준 구간."""
    from src.training.service import angle_trend

    username = _require_user(request)
    store = get_training()
    _check_training_owner(store.drill_owner(drill_id), username, "훈련 항목")
    drill = next((d for d in store.drills(username, include_archived=True) if d["id"] == drill_id), None)
    if not drill or not drill["metric"]:
        raise HTTPException(status_code=422, detail="판정 각도와 연결된 항목이 아닙니다.")
    response.headers["Cache-Control"] = "no-store"
    records = get_agent().memory.findings(memory_owner(username), limit=100)
    return angle_trend(records, drill["metric"])


@app.get("/training/library")
def training_library() -> dict:
    """훈련 라이브러리(골프 지식 문서의 연습 방법에서 정리한 항목, 교육용 더미 데이터). 게스트도 볼 수 있습니다."""
    from src.training.service import LEVELS, LIBRARY, LOCATIONS

    return {"categories": LIBRARY["categories"], "locations": list(LOCATIONS), "levels": LEVELS, "items": LIBRARY["items"]}


@app.post("/training/drills/from-library")
def training_from_library(req: FromLibraryRequest, request: Request) -> dict:
    username = _require_user(request)
    try:
        return get_training().add_from_library(username, req.key)
    except TrainingError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/training/plans")
def training_add_plan(req: PlanCreateRequest, request: Request) -> dict:
    username = _require_user(request)
    store = get_training()
    _check_training_owner(store.drill_owner(req.drill_id), username, "훈련 항목")
    try:
        return store.add_plan(username, req.drill_id, req.day)
    except TrainingError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.delete("/training/plans/{plan_id}")
def training_delete_plan(plan_id: str, request: Request) -> dict:
    username = _require_user(request)
    store = get_training()
    _check_training_owner(store.plan_owner(plan_id), username, "계획")
    return {"id": plan_id, "deleted": store.delete_plan(username, plan_id)}


@app.post("/training/routines")
def training_add_routine(req: RoutineCreateRequest, request: Request) -> dict:
    username = _require_user(request)
    store = get_training()
    _check_training_owner(store.drill_owner(req.drill_id), username, "훈련 항목")
    try:
        return store.add_routine(username, req.drill_id, req.weekdays, req.start, req.end)
    except TrainingError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.delete("/training/routines/{routine_id}")
def training_delete_routine(routine_id: str, request: Request) -> dict:
    username = _require_user(request)
    store = get_training()
    _check_training_owner(store.routine_owner(routine_id), username, "루틴")
    return {"id": routine_id, "deleted": store.delete_routine(username, routine_id)}


@app.post("/training/routines/{routine_id}/skip")
def training_skip_routine_day(routine_id: str, req: SkipRequest, request: Request) -> dict:
    """루틴에서 그 날 하루만 뺍니다."""
    username = _require_user(request)
    store = get_training()
    _check_training_owner(store.routine_owner(routine_id), username, "루틴")
    return {"id": routine_id, "day": req.day.isoformat(), "skipped": store.skip_routine_day(username, routine_id, req.day)}


@app.get("/training/calendar")
def training_calendar(request: Request, response: Response, year: int | None = None, month: int | None = None) -> dict:
    """월 달력(월요일 시작 주). 날마다 계획 항목의 완료·오늘·지남·예정과 계획 밖 연습, 이번 주·이번 달 달성률."""
    username = _require_user(request)
    today = datetime.date.today()
    year, month = year or today.year, month or today.month
    if not (2000 <= year <= 2100 and 1 <= month <= 12):
        raise HTTPException(status_code=422, detail="year 는 2000~2100, month 는 1~12 입니다.")
    response.headers["Cache-Control"] = "no-store"
    return get_training().calendar(username, year, month, today=today)


@app.post("/training/proposals/{proposal_id}/accept")
def training_accept_proposal(proposal_id: str, request: Request) -> dict:
    """추천 플랜을 받아 항목을 내 항목에 넣고 요일 루틴을 만듭니다."""
    username = _require_user(request)
    store = get_training()
    _check_training_owner(store.proposal_owner(proposal_id), username, "제안")
    try:
        return store.accept_proposal(username, proposal_id)
    except TrainingError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/training/proposals/{proposal_id}/dismiss")
def training_dismiss_proposal(proposal_id: str, request: Request) -> dict:
    username = _require_user(request)
    store = get_training()
    _check_training_owner(store.proposal_owner(proposal_id), username, "제안")
    return {"id": proposal_id, "dismissed": store.dismiss_proposal(username, proposal_id)}


@app.get("/admin/training")
def admin_training(request: Request, response: Response) -> dict:
    """[관리자] 진행 중 항목이 있는 모든 계정의 훈련 요약(항목 수·이번 주 연습일·연속·이번 주/달 달성률·마지막 연습일)."""
    _require_admin(request)
    response.headers["Cache-Control"] = "no-store"
    store = get_training()
    return {"users": [store.overview(owner) for owner in store.owners()]}


@app.get("/admin/training/{username}")
def admin_training_user(username: str, request: Request, response: Response, year: int | None = None, month: int | None = None) -> dict:
    """[관리자] 한 사용자의 훈련(항목·집계·최근 기록·제안)과 월 달력. 읽기 전용."""
    _require_admin(request)
    today = datetime.date.today()
    year, month = year or today.year, month or today.month
    if not (2000 <= year <= 2100 and 1 <= month <= 12):
        raise HTTPException(status_code=422, detail="year 는 2000~2100, month 는 1~12 입니다.")
    store = get_training()
    if username not in store.owners():
        raise HTTPException(status_code=404, detail="훈련 항목이 있는 사용자가 아닙니다.")
    response.headers["Cache-Control"] = "no-store"
    return {**_training_view(username), "calendar": store.calendar(username, year, month, today=today)}


@app.delete("/admin/training/routines/{routine_id}")
def admin_training_delete_routine(routine_id: str, request: Request) -> dict:
    _require_admin(request)
    return {"id": routine_id, "deleted": get_training().delete_routine_any(routine_id)}


@app.delete("/admin/training/drills/{drill_id}")
def admin_training_archive_drill(drill_id: str, request: Request) -> dict:
    _require_admin(request)
    return {"id": drill_id, "archived": get_training().archive_any(drill_id)}


@app.delete("/admin/training/logs/{log_id}")
def admin_training_delete_log(log_id: str, request: Request) -> dict:
    _require_admin(request)
    return {"id": log_id, "deleted": get_training().delete_log_any(log_id)}
