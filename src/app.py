"""POST /query 진입점 (FastAPI).

API 표준 규약
    요청:  {"question": "사용자 질의"}
    응답:  {"answer": ..., "contexts": [{"doc_id", "text"}], "trace": [{"step", "input", "output"}]}

이 서비스의 추가 필드 (표준 필드는 그대로 두고 덧붙입니다)
    요청:  image_base64(측면 어드레스 사진), video_base64(정면 스윙 영상), thread_id(대화 이어가기),
           model(코칭 모델 ID, GET /models 목록 중 하나), user_id(장기 기억 주인) — 모두 선택
    응답:  status(answered|no_answer|blocked), issue_codes, sources, agents(부른 sub-agent), model(코칭에 쓴 모델)

장기 기억 (user_id 별 자세 판정 기록, 대화방을 넘어 유지)
    GET    /memory/{user_id}  저장된 판정 기록 보기
    DELETE /memory/{user_id}  모두 지우기
    POST   /memory/{user_id}/move  {"to": 새 user_id}  기록 옮기기(닉네임을 정하거나 바꿀 때)

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

from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src import config
from src.agent import GolfCoachAgent, build_agent
from src.memory import USER_ID_PATTERN


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
    user_id: str | None = Field(default=None, pattern=USER_ID_PATTERN, description="장기 기억 주인. 같은 값을 보내면 대화방이 달라도 판정 기록이 이어집니다")

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


@lru_cache(maxsize=1)
def _cached_agent() -> GolfCoachAgent:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
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
def query(req: QueryRequest) -> dict:
    if req.image_base64 and req.video_base64:
        raise HTTPException(status_code=422, detail="사진과 영상은 한 번에 하나만 보내 주세요.")
    _check_media(req.image_base64, "사진")
    _check_media(req.video_base64, "영상")
    try:
        return get_agent().ask(
            req.question,
            image_base64=req.image_base64,
            video_base64=req.video_base64,
            thread_id=req.thread_id,
            model=req.model,
            user_id=req.user_id,
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
    user_id: str | None = Field(default=None, pattern=USER_ID_PATTERN)

    @field_validator("model")
    @classmethod
    def check_model(cls, value: str | None) -> str | None:
        return _allowed_model(value)


@app.post("/demo/fixture", response_model=QueryResponse)
def demo_fixture(req: DemoFixtureRequest) -> dict:
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
    try:
        return get_agent().ask(
            req.question, thread_id=req.thread_id, pose_fixture=fixture, model=req.model, user_id=req.user_id
        )
    except Exception as exc:
        raise _bedrock_http_error(exc) from exc


# ── 장기 기억 보기·지우기 ────────────────────────────────


def _check_user_id(user_id: str) -> None:
    import re

    if not re.fullmatch(USER_ID_PATTERN, user_id):
        raise HTTPException(status_code=422, detail="닉네임(user_id)은 띄어쓰기 없이 한글·영문·숫자·-·_ 2~64자입니다.")


@app.get("/memory/{user_id}")
def memory_list(user_id: str, response: Response) -> dict:
    """이 사용자의 장기 기억(자세 판정 기록, 최신순). 판정마다 바뀌므로 브라우저가 캐시하지 않게 합니다."""
    _check_user_id(user_id)
    response.headers["Cache-Control"] = "no-store"
    records = get_agent().memory.findings(user_id, limit=100)
    return {"user_id": user_id, "count": len(records), "findings": records}


class MemoryMoveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: str = Field(pattern=USER_ID_PATTERN)


@app.post("/memory/{user_id}/move")
def memory_move(user_id: str, req: MemoryMoveRequest) -> dict:
    """판정 기록을 다른 user_id 로 옮깁니다. 브라우저 임의 id → 닉네임으로 바꿀 때 씁니다."""
    _check_user_id(user_id)
    return {"from": user_id, "to": req.to, "moved": get_agent().memory.move(user_id, req.to)}


@app.delete("/memory/{user_id}")
def memory_clear(user_id: str) -> dict:
    """이 사용자의 장기 기억을 모두 지웁니다(대화방 기록은 그대로)."""
    _check_user_id(user_id)
    return {"user_id": user_id, "deleted": get_agent().memory.clear(user_id)}
