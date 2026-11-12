# SwingKid · 골프 입문 자세 코치

골프를 시작한 지 0~3개월 된 입문자가 **레슨이 없는 날에도 자기 자세를 확인**할 수 있게 돕는 멀티 agent 서비스입니다.
측면 어드레스 사진·정면 스윙 영상(GIF 포함)은 관절 각도를 숫자로 재서 어디가 기준을 벗어났는지 알려 주고,
골프 질문에는 내부 지식 문서와 웹 검색 근거 안에서만 답합니다. 근거가 없으면 지어내지 않고 모른다고 답합니다.

- 기획: [SERVICE.md](SERVICE.md) · 개발 기록: [HISTORY.md](HISTORY.md) · 평가 계획과 결과: [evaluation/EVAL_PLAN.md](evaluation/EVAL_PLAN.md)
- 평가 세트: [evaluation/test_queries.csv](evaluation/test_queries.csv) (20건: positive 8 · negative 4 · edge 5 · guardrail 3)

## 1. 주제

| 항목 | 내용 |
|---|---|
| 사용자 | 골프 입문 0~3개월 오른손잡이, 본인 포함 2~10명 |
| 문제 | 레슨 없는 날에는 내 자세가 맞는지 확인할 방법이 없고, 유튜브·블로그는 일반론이라 출처마다 말이 다름 |
| 해결 | 사진·영상 → 관절 각도 측정 → 교육용 기준값과 비교 → 근거 문서로 교정 방법 안내. 질문은 근거가 있을 때만 답함 |

## 2. 주요 기능

- **자세 판정**: 측면 어드레스 사진(척추 앞기울기·무릎 각도), 정면 스윙 영상·GIF(백스윙 정점의 왼팔 각도). 영상은 프레임별 각도 그래프가 재생 위치와 함께 움직입니다.
- **골프 질문**: 지식 문서 25개(기술·규칙·용어·코스·장비·연습·역사 등) 검색, 최신 정보는 MCP 웹 검색.
- **가드레일**: 의학 상담·구매/가격·개인정보·프롬프트 인젝션은 차단, 골프와 무관한 질문은 검색 없이 no_answer.
- **기억**: 같은 대화방에서는 앞 대화와 직전 판정을 이어서 답하고(단기), 로그인한 계정의 판정 기록은 대화방·기기를 넘어 남겨 지난 판정과 비교합니다(장기). 로그인하지 않아도(게스트) 질문과 판정은 쓸 수 있습니다.
- **훈련 관리**: 상단 '코칭 | 훈련' 전환. 골프 문서에서 정리한 훈련 라이브러리 25개(8개 분야), 월간 달력으로 날짜 배치·요일 루틴을 잡고 완료를 체크하며 이번 주·이번 달 달성률을 봅니다. 판정에서 고칠 점이 나오면 규칙으로 2주 플랜(월·수·금)을 추천하고, 판정과 연결된 항목은 각도 변화 그래프를 보여 줍니다. "요즘 연습 어때?"에는 코드가 집계한 연습 요약을 근거로 답합니다.
- **웹 화면**: 질문·사진·영상 첨부, 샘플 사진·영상 선택, 판정 막대·각도 그래프·근거·처리 단계 표시, 아키텍처 다이어그램(모듈을 누르면 실제 코드).

## 3. 설계

```
POST /query
   │
   ▼
 guard ──(의학·구매/가격·인젝션·개인정보)──▶ blocked  (guard_ 코드 1개, 고정 거절 문구)
   ├─(골프와 무관)──▶ no_answer  (검색·웹·LLM 없이 고정 문구)
   ▼
 supervisor ──────────┬─▶ swing_agent     사진·영상·GIF → pose(포즈 추정 → 각도 판정)
   ▲  (규칙 기반 라우팅) ├─▶ knowledge_agent 골프 지식 → retrieve(내부 문서 25개)
   │                    └─▶ info_agent      최신 정보·문서 밖 정보 → MCP 도구 golf_web_search
   └──── sub-agent 는 결과를 들고 supervisor 로 돌아옴 ────┘
 supervisor 의 다음 결정
   - 근거가 있으면 ─────────────────────▶ coach(Bedrock Claude 가 근거로 답변 작성) ──▶ answered
   - knowledge_agent 가 근거를 못 찾으면 ─▶ info_agent 로 한 번 더(웹 검색)
   - '지난번 판정' 질문 ─────────────────▶ 장기 기억을 근거로 바로 coach
   - 자세 인식 실패 · 웹에서도 근거 없음 ──▶ no_answer (고정 문구, LLM 호출 없음)
```

LLM(생성) 호출은 **coach 한 곳**뿐입니다. sub-agent 는 도구만 쓰고, 문서 검색의 의미 검색에 Titan 임베딩을 씁니다.
화면 맨 아래와 `/architecture` 에서 같은 구조를 다이어그램으로 볼 수 있고, 모듈을 누르면 해당 함수의 실제 코드가 줄 번호와 함께 열립니다.

| 파일 | 역할 |
|---|---|
| [src/agent.py](src/agent.py) | 메인 그래프(LangGraph): 가드 → supervisor(라우팅) → sub-agent → coach. 단기 기억(체크포인터) |
| [src/subagents.py](src/subagents.py) | sub-agent 3개: SwingAgent(자세 판정), KnowledgeAgent(문서 RAG), InfoAgent(웹 검색) |
| [src/guard.py](src/guard.py) | 규칙 기반 가드레일 (같은 질문은 항상 같은 결과) |
| [src/tools.py](src/tools.py) | 관절 각도 계산, 문제 코드 판정, 백스윙 정점 검출, MediaPipe 추정기(영상·GIF) |
| [src/retriever.py](src/retriever.py) | RAG: BM25(kiwipiepy 형태소), Titan/Chroma, 라이브용 하이브리드(BM25 + Chroma) |
| [src/web_search.py](src/web_search.py) · [src/mcp_server.py](src/mcp_server.py) | 웹 검색: MCP 클라이언트(langchain-mcp-adapters) → stdio MCP 서버 `golf_web_search` → DuckDuckGo(ddgs, API 키 불필요) |
| [src/memory.py](src/memory.py) | 장기 기억: 로그인 계정별 판정 기록(코드·각도)을 LangGraph Store 에 저장 |
| [src/history.py](src/history.py) | 코칭 내역: 로그인 계정별 대화 목록 저장, 대화방(thread_id)마다 주인을 정해 남의 대화방을 읽거나 이어 쓰지 못하게 함 |
| [src/training.py](src/training.py) · [data/training_library.json](data/training_library.json) | 훈련 관리: 라이브러리(문서 기반 25개), 내 항목·기록, 날짜 배치·요일 루틴·하루 빼기, 월 달력(완료·오늘·지남·예정)·달성률, 판정 기반 2주 플랜 제안, 각도 변화, 코칭용 연습 요약. 집계·판정은 모두 코드 |
| [src/avatar.py](src/avatar.py) | 캐릭터 아이콘: 가입할 때 부품(몸·배경색, 귀·모자·새싹 등, 눈, 입, 볼터치, 무늬)을 무작위로 골라 저장하고 SVG 로 그림 |
| [src/auth.py](src/auth.py) | 계정: 아이디·비밀번호 가입·로그인·로그아웃(scrypt 해시, 세션 토큰은 SHA-256 만 저장, 표준 라이브러리만 사용) |
| [src/app.py](src/app.py) | FastAPI: `POST /query`, 화면, 샘플·기억·아키텍처 경로 |
| [src/llm_judge.py](src/llm_judge.py) | 평가용 LLM 심사(고정 루브릭·고정 모델·temperature 0) |
| [src/evaluate.py](src/evaluate.py) · [src/calibrate.py](src/calibrate.py) | 자체 평가(코드 채점·라이브·LLM 심사·전후 비교), 검색 기준값·지표 확인 |
| [src/arch_code.py](src/arch_code.py) · [src/static/arch.js](src/static/arch.js) | 아키텍처 다이어그램과 모듈별 코드 보기 |
| [src/static/index.html](src/static/index.html) | 웹 화면 |
| [data/docs/](data/docs/) | 교육용 골프 지식 문서 25개 |
| [data/fixtures/](data/fixtures/) | 채점용 더미 관절 좌표 7개, 가짜 웹 검색 결과 |
| [data/test_photos/](data/test_photos/) · [data/test_videos/](data/test_videos/) | 시험용 실제 사진 7장·영상 4개 (출처·라이선스는 각 폴더 SOURCES.md) |

**고정 스택**: Bedrock Claude Sonnet 4.5 (`us.anthropic.claude-sonnet-4-5-20250929-v1:0`) · Titan v2 임베딩 · `us-east-1` · Chroma(`./chroma_db`, `sds_policies`) · SqliteSaver(`checkpoints.sqlite`). 그 밖에 MediaPipe·OpenCV(자세), SqliteStore(장기 기억), MCP(웹 검색)를 씁니다.

### 문제 코드와 교육용 기준값

| 코드 | 입력 | 측정 | 기준(교육용) | 근거 문서 |
|---|---|---|---|---|
| `spine_tilt_out_of_range` | 측면 어드레스 사진 | 척추 앞기울기 | 30~45° | address.md |
| `knee_flex_out_of_range` | 측면 어드레스 사진 | 무릎 각도 | 145~165° | address.md |
| `bent_lead_arm` | 정면 스윙 영상·GIF | 백스윙 정점 왼팔 각도 | 160° 이상 | backswing.md |

## 4. 핵심 의사결정 2가지

1. **스윙 판정은 LLM이 아니라 결정적인 각도 계산이 한다.**
   포즈 추정으로 관절 좌표를 얻고 순수 함수로 각도를 계산해 문제 코드를 냅니다. LLM은 판정 결과를 받아 설명 문장만 씁니다.
   같은 입력에는 항상 같은 판정이 나오고, 판정 근거(측정 각도·기준값·근거 문서)를 그대로 보여 줄 수 있습니다. 고정 모델이 영상을 직접 받지 못한다는 제약도 이 구조로 해결했습니다.

2. **라우팅·가드레일은 규칙으로, 외부 부품은 모두 주입 가능하게 만들어 실제 API 없이 채점한다.**
   supervisor 라우팅과 가드레일은 규칙 기반이라 같은 질문은 항상 같은 경로를 탑니다. LLM·검색기·웹 검색기·포즈 추정기·체크포인터는 생성자 인자로 받아, 자동 채점은 가짜 모델 + BM25 + 가짜 웹 검색기 + 더미 관절 좌표로 돕니다.
   BM25도 실제 문서를 검색하므로 '근거가 없으면 모른다고 답하기'까지 오프라인에서 검증됩니다. 근거 판단 기준값(점수 2.5 이상 + 질문 단어의 IDF 가중 문서 포함 비율 0.5 이상, 라이브 Chroma 관련도 0.2 이상)은 평가 세트와 별개인 보정 질문 50개([data/calibration.json](data/calibration.json))로 정했습니다.

## 5. API

표준 규약(`question` → `answer`, `contexts`, `trace`)을 그대로 따르고, 이 서비스에 필요한 필드를 덧붙였습니다.

```http
POST /query
Content-Type: application/json

{"question": "제 어드레스 자세 봐주세요", "image_base64": "<측면 사진>", "thread_id": "room-1"}
```

```json
{
  "answer": "척추 앞기울기가 52°로 교육용 기준(30~45°)보다 커서 상체가 많이 숙여졌어요…",
  "contexts": [{"doc_id": "address.md", "text": "# 어드레스 자세 …"}],
  "trace": [
    {"step": "guard", "input": "제 어드레스 자세 봐주세요", "output": "통과"},
    {"step": "supervisor", "input": "제 어드레스 자세 봐주세요", "output": "swing_agent ← 사진·영상 자세 판정"},
    {"step": "pose", "input": "photo", "output": "{'issue_codes': ['spine_tilt_out_of_range'], 'spine_tilt': 52.0, 'knee_flex': 155.0}"},
    {"step": "coach", "input": "제 어드레스 자세 봐주세요", "output": "척추 앞기울기가 52°로…"},
    {"step": "memory", "input": "골프키드", "output": "이번 판정 기록 저장"}
  ],
  "status": "answered",
  "issue_codes": ["spine_tilt_out_of_range"],
  "sources": ["address.md"],
  "agents": ["swing_agent"],
  "angle_series": null,
  "model": "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
}
```

| 필드 | 구분 | 설명 |
|---|---|---|
| `question` | 표준 요청 | 사용자 질의 (필수) |
| `image_base64` / `video_base64` | 추가 요청 | 측면 어드레스 사진 / 정면 스윙 영상(mp4·webm·mov·GIF). 한 번에 하나, 디코딩 후 10MB 이하 |
| `thread_id` | 추가 요청 | 같은 값을 보내면 대화가 이어집니다(단기 기억) |
| `model` | 추가 요청 | 코칭 모델 ID (`GET /models` 목록 중 하나, 없으면 기본 모델) |
| `status` | 추가 응답 | `answered` · `no_answer` · `blocked` |
| `issue_codes` | 추가 응답 | 스윙 문제 코드, `pose_not_detected`, 또는 `guard_` 코드 1개 |
| `sources` | 추가 응답 | 근거 문서 ID 또는 웹 출처 URL |
| `agents` | 추가 응답 | supervisor 가 부른 sub-agent (순서대로) |
| `angle_series` | 추가 응답 | 영상 판정일 때 프레임별 왼팔 각도 `{metric, t, values, apex_index, threshold}` |
| `model` | 추가 응답 | 답변에 쓴 모델 |

장기 기억 주인은 요청 본문이 아니라 로그인 세션 쿠키(`swingkid_session`)로 정합니다. 로그인하지 않았으면 장기 기억을 읽거나 쓰지 않습니다.

오류: 모르는 필드(예: 채점용 `pose_fixture`, 예전 `user_id`)·base64 오류·사진과 영상 동시 전송·허용 목록 밖 모델은 422, 10MB 초과는 413, Bedrock 사용량 한도는 429.

**그 밖의 경로**

| 경로 | 설명 |
|---|---|
| `GET /` · `GET /architecture` | 웹 화면 · 아키텍처 다이어그램(크게 보기) |
| `GET /architecture/code/{module}` | 다이어그램 모듈의 중요 코드(실제 소스에서 함수 단위로) |
| `GET /models` · `GET /health` | 코칭 모델 목록 · 상태 |
| `GET /samples/media` · `GET /samples/media/{name}` | 샘플 사진·영상 목록 · 파일 |
| `POST /auth/signup` · `POST /auth/login` · `POST /auth/logout` · `GET /auth/me` | 가입(바로 로그인) · 로그인 · 로그아웃 · 지금 로그인한 아이디. 본문 `{username, password}`, 세션은 HttpOnly 쿠키(30일) |
| `GET /auth/avatar.svg` | 내 캐릭터 아이콘(로그인하지 않으면 401) |
| `GET /admin/threads` · `DELETE /admin/threads/{thread_id}` · `GET /admin/avatars/{username}.svg` | [관리자] 모든 계정의 코칭 보기 · 지우기 · 주인 캐릭터 (관리자가 아니면 403) |
| `GET /memory` · `DELETE /memory` | 내 장기 기억 보기 · 지우기 (로그인하지 않으면 401) |
| `GET /threads` · `PUT·DELETE /threads/{thread_id}` · `DELETE /threads` | 내 코칭 내역 보기 · 하나 저장/지우기 · 모두 지우기 (로그인하지 않으면 401, 남의 대화방이면 403) |
| `GET /training/library` | 훈련 라이브러리(게스트도 볼 수 있음) |
| `GET /training` · `GET /training/calendar?year=&month=` | 내 항목·집계·최근 기록·오늘 계획·추천 제안 · 월 달력과 이번 주/달 달성률 |
| `POST /training/drills` · `POST /training/drills/from-library` · `DELETE /training/drills/{id}` | 직접 만들기 · 라이브러리에서 넣기 · 목록에서 내리기(기록은 남음) |
| `POST /training/drills/{id}/logs` · `DELETE /training/logs/{id}` · `GET /training/drills/{id}/trend` | 기록(날짜·횟수·메모) · 지우기 · 판정 각도 변화 |
| `POST·DELETE /training/plans` · `POST·DELETE /training/routines` · `POST /training/routines/{id}/skip` | 날짜 배치 · 요일 루틴 · 루틴 하루 빼기 |
| `POST /training/proposals/{id}/accept` · `.../dismiss` | 판정 추천 2주 플랜 받기(루틴 생성) · 닫기 |
| `GET /admin/training` · `GET /admin/training/{username}` · `DELETE /admin/training/{drills,logs,routines}/{id}` | [관리자] 사용자 훈련 현황 · 한 사용자의 달력·항목·기록 · 지우기 |
| `GET /demo/samples` · `POST /demo/fixture` | 추천 질문 목록 · 더미 관절 좌표로 판정(오프라인, 또는 라이브에서 `GOLF_DEMO=1`일 때만) |

**입력 전제**: 사진은 **측면**(뒤에서 비구 방향으로) 어드레스 자세, 영상은 **정면** 스윙, **오른손잡이**만 판정합니다(실행 중에 검사하지 않음). 사람이 없거나 필요한 관절이 잘 안 보이면(인식 신뢰도 0.5 미만) 판정하지 않고 다시 찍어 달라고 안내합니다.

## 6. 시작하기

```bash
git clone https://github.com/shkim0001/swingkid.git
cd swingkid
python -m venv .venv && source .venv/Scripts/activate   # macOS·Linux: source .venv/bin/activate
pip install -r requirements.txt
```

라이브 모드에는 AWS Bedrock 자격 증명이 필요합니다. 프로젝트 폴더에 `.env` 를 만듭니다(저장소에는 올리지 않습니다).

```
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-east-1
```

```bash
./run.sh test        # 단위 테스트 (실제 API 호출 없음)
./run.sh eval        # 평가 세트 오프라인 자체 평가
./run.sh offline     # 가짜 모델로 서버 실행 (AWS 불필요)
./run.sh live        # 실제 Bedrock · Titan/Chroma · MCP 웹 검색 · MediaPipe
GOLF_DEMO=1 ./run.sh live   # 라이브 + 더미 관절 좌표 데모 경로
```

Windows PowerShell: `python -m pytest -q`, `python -m src.evaluate --detail`, `$env:GOLF_MODE="offline"; python -m uvicorn src.app:app --port 8000`.
서버를 띄운 뒤 http://localhost:8000 을 엽니다.

처음 라이브로 실행하면 Chroma 검색 색인(`chroma_db/`)을 문서로 만들고, 처음 사진·영상을 분석할 때 포즈 모델 파일(`data/models/`, 약 9MB)을 공식 주소에서 내려받습니다. 둘 다 저장소에는 없습니다. 대화 기록(`checkpoints.sqlite`)과 장기 기억(`memory.sqlite`)도 실행 중에 생깁니다.

## 7. 웹 화면

- **배치**: 왼쪽 사이드바(로그인·장기 기억·판정 기준), 가운데 대화창. 입력창 바로 위 가로 칩 줄이 추천 질문(`test_queries.csv`)이며, 누르면 입력창에 채워집니다.
- **코칭**: 가운데 카드가 지금 대화(thread_id)입니다. 왼쪽 '코칭 목록'에 내 코칭이 최근순으로 쌓이고, 누르면 가운데에서 열립니다. 아래쪽 '지난 코칭'에도 작은 카드로 보입니다. 로그인하면 코칭 내역이 계정별로 서버에 저장되어 자기 것만 보이고 다른 기기에서도 이어집니다. 게스트 내역은 이 브라우저(localStorage)에만 남습니다. 다른 계정의 대화방(thread_id)으로는 질문할 수 없습니다(403).
- **첨부**: "사진·영상" 버튼·끌어다 놓기, 또는 '사진·영상 샘플'에서 시험용 파일을 고릅니다. 영상·GIF는 대화 카드에서 자동 재생·반복되고, 원본은 브라우저(IndexedDB)에 저장되어 새로고침해도 재생됩니다(서버에는 올리지 않으므로 다른 기기에서는 영상이 재생되지 않습니다).
- **답변**: 상태·문제 코드·관절 각도 막대(초록 구간이 교육용 기준)·근거·처리 단계·응답 JSON. 영상 판정은 '왼팔 각도 변화' 그래프가 재생 위치에 맞춰 움직이고, 그래프를 누르면 그 장면으로 이동합니다.
- **로그인**: 프로필 카드에서 아이디(띄어쓰기 없이 한글·영문·숫자·`-`·`_` 2~32자)와 비밀번호(8자 이상)로 가입하거나 로그인합니다. 로그인하면 다른 기기에서도 판정 기록이 이어지고 '장기 기억' 줄이 보입니다. 가입할 때 계정마다 무작위 캐릭터 아이콘이 정해져 프로필과 코칭 카드에 보입니다. 게스트는 질문·판정만 되고 기록은 남지 않습니다.
- **관리자**: `.env` 에 `ADMIN_PASSWORD=<8자 이상>` 을 넣으면 서버가 `admin` 계정을 그 비밀번호로 만듭니다(화면에서는 `admin` 으로 가입할 수 없음). `admin` 으로 로그인하면 왼쪽 코칭 목록의 '모든 사용자' 탭과 화면 아래 '모든 사용자 코칭' 작은 카드에서 다른 계정의 코칭을 읽기 전용으로 열고 지울 수 있습니다. 남의 코칭을 이어 쓰지는 못합니다.
- **모바일(760px 이하)**: 상단 바는 ☰ · 로고 · 내 캐릭터만 두고, 프로필·코칭 목록·판정 기준·코칭 모델 선택은 ☰(또는 캐릭터)로 여는 왼쪽 서랍에 넣었습니다. 입력창은 한 줄([샘플] [📷] [입력칸] [↑])이며, 샘플 선택은 '샘플' 버튼으로 펼칩니다. 추천 질문 칩은 새 코칭 화면에서만 보입니다.
- **훈련**: 상단 '훈련'(주소 `#training`). 이번 주·이번 달 달성률과 연속 연습일, 판정 추천 플랜(받기·닫기), 월간 달력(점: 완료·오늘·지남·예정·계획 밖 연습), 고른 날의 계획(완료 체크·기록·메모·빼기·이 날에 넣기), 내 훈련 항목(루틴·기록 보기·일정 잡기), 훈련 라이브러리(분야·장소 필터, 일정에 넣기). 항목 상세에서 기록과 각도 변화 그래프를 봅니다. 코칭 화면 왼쪽 '오늘의 훈련'에서도 오늘 계획을 체크할 수 있고, 판정 답변에 추천 플랜 안내가 붙습니다. 관리자는 '사용자 훈련'에서 사용자별 달력·기록을 읽기 전용으로 보고 지웁니다.
- **코칭 모델**: 상단에서 Bedrock 모델 10개(Claude Sonnet 4.5·4.6, Haiku 4.5, Amazon Nova) 중 고릅니다. 답변 작성에만 쓰이고, 검색·판정은 같습니다. 오프라인에서는 항상 가짜 모델이 답합니다.
- **아키텍처**: 화면 맨 아래 'SwingKid는 이렇게 동작해요'. 보라색이 LLM·모델 호출이며, 모듈을 누르면 실제 코드가 열립니다.

## 8. 평가

계획은 [evaluation/EVAL_PLAN.md](evaluation/EVAL_PLAN.md), 회차별 보고서는 [1차(기준선 v1)](evaluation/round1_report.md) · [2차(v2)](evaluation/round2_report.md) · [3차(v3)](evaluation/round3_report.md) · [4차(v4)](evaluation/round4_report.md) 에 있습니다. 세 겹으로 봅니다.

| 겹 | 무엇을 | 명령 |
|---|---|---|
| A. 코드 채점 | status·issue_codes·sources·agents·처리 단계·고정 문구 | `python -m src.evaluate` (오프라인, 실제 API 없음) |
| B. LLM 심사 | 기계로 못 보는 기대 특성·금지 항목, 근거 밖 내용, 질문 적합성 | `python -m src.evaluate --live --judge` |
| C. 검색 진단 | 기대 문서 재현율·1위 비율·MRR | 평가 결과에 함께, `python -m src.calibrate [--live]` |

- LLM 심사는 judge 모델(Sonnet 4.5)·temperature 0·루브릭을 고정하고, 같은 답을 두 번 채점해 다르면 세 번째로 다수결합니다. 낮은 점수·위반·판정 불일치는 사람 검토 대상으로 모읍니다.
- 결과를 저장하고(`--save --label v2`) 이전 결과와 비교합니다(`--compare evaluation/results/<이전>.json`). 고쳐진 문항과 회귀(PASS → FAIL)를 함께 보여 줍니다.
- 평가셋 조각 앞에 `오프라인:` / `라이브:` 를 붙이면 그 모드에서만 확인합니다(실행마다 바뀌는 웹 검색 결과 대응).

| 회차 (2026-11-12, 라이브) | 코드 채점 | judge 평균 | 정답 | 근거 밖 내용 없음 | 위반 |
|---|---|---|---|---|---|
| v1 기준선 | 19/20 | 4.09 | 73% | 82% | 3 |
| v2 프롬프트·검색 개선 | 18/20 | 4.67 | 92% | 83% | 2 |
| v3 judge 수정·모드별 기대값 | 19/20 | 4.92 | 100% | 100% | 0 |
| v4 웹 검색 대체 경로·가드레일 보강·범위 프롬프트 정리 | 20/20 | 5.00 | 100% | 100% | 0 |

오프라인 코드 채점은 20/20, 보정 질문 50개 오류 0건입니다. 3차까지 남았던 웹 검색 환경 실패(6번)는 검색 엔진 대체 경로로 4차에서 해결했습니다. 평가는 `--strict-model` 로 코칭 모델 대체 경로를 꺼서 회차 간 모델 조건을 같게 둡니다.

**가드레일 스트레스 세트** (`evaluation/guardrail_stress.csv` 18건): 표현을 바꾼 공격 12건(돌려 말한 통증, '뭐 사요'·브랜드 비교, 역할 바꾸기·가짜 SYSTEM 메시지·지시문 노출 요청 등)과 막으면 안 되는 정상 질문 6건입니다. 가드 보강 전 8/18(3건은 실제로 답변이 나감) → 보강 후 18/18(라이브도 18/18). 실행: `python -m src.evaluate --set evaluation/guardrail_stress.csv`

## 9. 한계

- 각도 기준값과 골프 문서는 모두 **교육용 더미 데이터**입니다. 실제 코칭 기준이 아닙니다.
- 2D 관절 좌표라 촬영 각도가 달라지면 측정값이 달라지고, 영상은 백스윙 정점 근처 각도가 프레임마다 ±8° 정도 흔들립니다. GIF는 프레임을 덜어 내고 색을 줄여 원본 영상과 판정이 다를 수 있습니다.
- 사진 한 장으로는 스윙 흐름(템포·순서)을 볼 수 없어, 사진은 어드레스 자세만 판정합니다.
- 웹 검색(DuckDuckGo)은 결과가 실행마다 다르고 가끔 시간 초과·빈 결과가 납니다.
- 계정 기능은 가입·로그인·로그아웃만 있습니다. 비밀번호 변경·찾기, 회원 탈퇴, 로그인 시도 횟수 제한은 없습니다.
- 예전 닉네임 방식으로 쌓인 판정 기록은 계정으로 옮기지 않습니다.
- 훈련 라이브러리와 추천 플랜(2주·월·수·금)은 교육용 더미 데이터·제안값입니다. 알림·리마인더, 레슨 프로 계정, 플랜 공유, 외부 달력 연동은 없습니다. 연습 현황 질문의 라이브 Bedrock 답변은 아직 심사(LLM judge)하지 않았습니다.
- 골프 규칙 문서는 2023년판 기준입니다. 새 개정판이 나오면 문서를 갱신해야 합니다.
