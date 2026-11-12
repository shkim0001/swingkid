"""골프 입문 자세 코치 — 공통 설정값.

이 파일에는 '바꾸면 채점 결과가 달라지는' 값만 모아 둡니다.
- 고정 스택(모델·임베딩·리전·벡터DB·체크포인터)은 상위 저장소 AGENTS.md 규약을 그대로 따릅니다.
- 고정 응답 문구는 채점기가 문구 앞부분(startswith)으로 확인하므로 함부로 바꾸지 않습니다.
- 각도 기준값은 '교육용 기준값'입니다. 실제 코칭 기준이 아닙니다.
"""

from __future__ import annotations

from pathlib import Path

# ── 경로 ────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = PROJECT_ROOT / "data" / "docs"
FIXTURES_DIR = PROJECT_ROOT / "data" / "fixtures"
CALIBRATION_FILE = PROJECT_ROOT / "data" / "calibration.json"

# 라이브 사진·영상 분석용 MediaPipe 포즈 모델 (없으면 처음 쓸 때 내려받음, git 제외)
POSE_MODEL_PATH = PROJECT_ROOT / "data" / "models" / "pose_landmarker_full.task"
POSE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_full/float16/latest/pose_landmarker_full.task"
)

# ── 고정 스택 (임의 변경 금지) ──────────────────────────────
MODEL_ID = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
EMBED_MODEL_ID = "amazon.titan-embed-text-v2:0"
REGION = "us-east-1"
PERSIST_DIR = "./chroma_db"
COLLECTION = "sds_policies"
CHECKPOINT_DB = "checkpoints.sqlite"
# 장기 기억(사용자별 자세 판정 기록, LangGraph SqliteStore). 대화방 기억(checkpoints.sqlite)과 따로 둡니다.
MEMORY_DB = "memory.sqlite"
# 계정(아이디·비밀번호 해시·세션 토큰 해시, src/auth.py). 라이브 모드에서만 파일로 저장합니다.
ACCOUNTS_DB = "accounts.sqlite"   # 코칭 내역(src/history.py)도 같은 파일에 저장
MAX_THREAD_BYTES = 2 * 1024 * 1024   # 코칭 하나(질문·답변·사진 미리보기 JSON)의 최대 크기
MEMORY_RECENT = 5   # 코칭 때 참고하는 최근 판정 기록 수

# ── 웹 화면에서 고를 수 있는 코칭 모델 (Bedrock, 2026-11-11 호출 확인) ──
# 기본값은 고정 스택 MODEL_ID 입니다. 모델을 지정하지 않은 요청과 자동 채점은 기본값만 씁니다.
AVAILABLE_MODELS = [
    {"id": MODEL_ID, "label": "Claude Sonnet 4.5 · us (기본)"},
    {"id": "global.anthropic.claude-sonnet-4-5-20250929-v1:0", "label": "Claude Sonnet 4.5 · global"},
    {"id": "us.anthropic.claude-sonnet-4-6", "label": "Claude Sonnet 4.6 · us"},
    {"id": "global.anthropic.claude-sonnet-4-6", "label": "Claude Sonnet 4.6 · global"},
    {"id": "us.anthropic.claude-haiku-4-5-20251001-v1:0", "label": "Claude Haiku 4.5 · us"},
    {"id": "global.anthropic.claude-haiku-4-5-20251001-v1:0", "label": "Claude Haiku 4.5 · global"},
    {"id": "us.amazon.nova-pro-v1:0", "label": "Amazon Nova Pro · us"},
    {"id": "us.amazon.nova-2-lite-v1:0", "label": "Amazon Nova 2 Lite · us"},
    {"id": "global.amazon.nova-2-lite-v1:0", "label": "Amazon Nova 2 Lite · global"},
    {"id": "us.amazon.nova-lite-v1:0", "label": "Amazon Nova Lite · us"},
]
AVAILABLE_MODEL_IDS = {m["id"] for m in AVAILABLE_MODELS}

# ── 응답 상태값 ─────────────────────────────────────────────
STATUS_ANSWERED = "answered"
STATUS_NO_ANSWER = "no_answer"
STATUS_BLOCKED = "blocked"

# ── 고정 응답 문구 ──────────────────────────────────────────
# 문서에 근거가 없을 때. 상위 저장소 Day2 관례와 같은 문장입니다.
NO_ANSWER_TEXT = "제공된 문서에서 답을 찾을 수 없습니다."
# 자세를 인식하지 못했을 때는 위 문장으로 시작하고 재촬영 안내를 붙입니다.
POSE_RETRY_TEXT = "분석할 수 없습니다, 다시 찍어 주세요. (사진은 측면, 영상은 정면에서 전신이 보이게 찍어 주세요)"
# 가드레일 차단 문구.
BLOCKED_TEXT = "골프 자세·기술 코칭 범위를 벗어난 질문이라 답할 수 없습니다."
MEDICAL_SUFFIX = "통증이나 부상은 전문가와 상담하세요."
# 골프와 무관한 질문은 차단하지 않고 no_answer 로 끝냅니다. NO_ANSWER_TEXT 뒤에 붙입니다.
OFF_TOPIC_SUFFIX = "골프와 관련된 질문에만 답할 수 있습니다."
# 이전 판정을 물었는데 장기 기억에 기록이 없을 때 NO_ANSWER_TEXT 뒤에 붙입니다.
MEMORY_EMPTY_TEXT = "저장된 이전 자세 판정 기록이 없습니다. 사진이나 영상으로 판정을 받으면 기록이 쌓입니다."

# ── 문제 코드 ───────────────────────────────────────────────
SPINE_TILT = "spine_tilt_out_of_range"
KNEE_FLEX = "knee_flex_out_of_range"
BENT_LEAD_ARM = "bent_lead_arm"
POSE_NOT_DETECTED = "pose_not_detected"

GUARD_MEDICAL = "guard_medical"
GUARD_PURCHASE = "guard_purchase"
GUARD_OFF_TOPIC = "guard_off_topic"

# ── 교육용 기준값 (도) ──────────────────────────────────────
SPINE_TILT_RANGE = (30.0, 45.0)     # 측면 어드레스 척추 앞기울기
KNEE_FLEX_RANGE = (145.0, 165.0)    # 측면 어드레스 무릎 각도(엉덩이-무릎-발목)
LEAD_ARM_MIN = 160.0                # 정면 백스윙 정점 왼팔(어깨-팔꿈치-손목)
MIN_VISIBILITY = 0.5                # 필요한 관절의 인식 신뢰도 하한

# 문제 코드가 근거로 삼는 문서. 미디어 경로는 검색 대신 이 표로 근거 문서를 정합니다.
MEDIA_SOURCE = {"photo": "address.md", "video": "backswing.md"}

# ── 입력 제한 ───────────────────────────────────────────────
MAX_MEDIA_BYTES = 10 * 1024 * 1024  # 디코딩 후 10MB

# ── 검색 ────────────────────────────────────────────────────
# 4: 평가 v1 에서 '드라이버 그립' 질문이 grip.md 의 '양손 연결(오버래핑·인터로킹)' 조각(BM25 4위)을 놓쳐 3 → 4
TOP_K = 4
# BM25 최고 점수가 이 값보다 낮으면 '문서에 근거 없음'으로 봅니다.
# data/calibration.json 의 보정용 질문(평가셋과 별개)으로 정한 값입니다. (재현: python -m src.calibrate)
# 문서 25개(조각 100여 개)로 늘리며 다시 보정: '버디가 뭐예요?'처럼 단어 하나짜리 정의 질문도 잡도록 3.5 → 2.5
BM25_MIN_SCORE = 2.5
# 1위 조각이 속한 문서가 질문 단어를 희귀도(IDF) 가중 비율로 이 값 이상 담고 있어야 근거로 인정합니다.
# 흔한 단어만 겹치고 핵심어가 문서에 없는 질문('골프장 근처 맛집')을 걸러 냅니다. (단순 개수 비율 0.3 → IDF 가중 0.5)
BM25_MIN_COVERAGE = 0.5
# 라이브 Chroma(Titan v2, L2 거리 기반 관련도) 근거 하한. calibration.json 50문항을 라이브로 재서 정함(2026-11-11):
# 모를 질문(최신 정보 규칙에 안 걸리는 것) 최고 0.179, 답할 질문 중앙 0.354 → 0.2 (0.35 일 때는 답할 질문 절반이 근거 없음)
# 짧은 용어 질문은 의미 검색 점수가 낮으므로 라이브는 BM25 와 함께 씁니다(HybridRetriever).
CHROMA_MIN_RELEVANCE = 0.2

# ── 멀티 agent ──────────────────────────────────────────────
# supervisor 가 고르는 sub-agent 이름. trace 의 supervisor 단계와 응답의 agents 필드에 그대로 나옵니다.
SWING_AGENT = "swing_agent"          # 사진·영상 자세 판정 (포즈 추정기 + 각도 판정기)
KNOWLEDGE_AGENT = "knowledge_agent"  # 내부 골프 지식 문서 검색 (RAG)
INFO_AGENT = "info_agent"            # 웹 검색 (최신 결과·순위·문서에 없는 골프 정보)
WEB_TOP_K = 4                        # 웹 검색 결과를 근거로 쓸 최대 개수
# 웹 검색 엔진을 이 순서로 시도합니다(ddgs 백엔드). 2026-11-12 측정(질문 3개 × 2회, 키워드/문장):
# yahoo 5/6·3/6(1~2초), yandex 4/6·4/6, google 4/6·1/6, bing 3/6·4/6, brave·mojeek·wikipedia 0, duckduckgo 는 이 네트워크에서 시간 초과.
# 3차 평가 6번 실패 원인이 DuckDuckGo 시간 초과였습니다. 한 엔진이 막혀도 다음 엔진으로 넘어갑니다.
WEB_BACKENDS = ("yahoo", "yandex", "google", "bing")
WEB_ATTEMPT_TIMEOUT = 6     # 엔진 한 번 호출 제한(초)
WEB_TOTAL_TIMEOUT = 40      # 검색 한 번 전체 제한(초). MCP 호출 제한(60초) 안에 끝나도록
