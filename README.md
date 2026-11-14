# SwingKid · 골프 입문 자세 코치

골프를 시작한 지 0~3개월 된 입문자가 **레슨이 없는 날에도 자기 자세를 확인**할 수 있게 돕는 멀티 agent 서비스입니다.
측면 어드레스 사진과 정면 스윙 영상(GIF 포함)은 관절 각도를 숫자로 재서 어디가 기준을 벗어났는지 알려 줍니다.
골프 질문에는 내부 지식 문서와 웹 검색 근거 안에서만 답하고, 근거가 없으면 지어내지 않고 모른다고 답합니다.

- 기획: [SERVICE.md](SERVICE.md) · 작업 목록: [TASKS.md](TASKS.md) · 개발 기록: [HISTORY.md](HISTORY.md)
- 평가 계획과 결과: [evaluation/EVAL_PLAN.md](evaluation/EVAL_PLAN.md) · 평가 세트: [evaluation/test_queries.csv](evaluation/test_queries.csv) (20건: positive 8 · negative 4 · edge 5 · guardrail 3)
- 코드 구조와 작업 규칙: [CLAUDE.md](CLAUDE.md)

## 목차

1. [주제](#1-주제)
2. [주요 기능](#2-주요-기능)
3. [설계](#3-설계)
4. [핵심 의사결정 2가지](#4-핵심-의사결정-2가지)
5. [시작하기](#5-시작하기)
6. [서버 올리기: 로컬 / Cloudflare](#6-서버-올리기-로컬--cloudflare)
7. [API](#7-api)
8. [웹 화면](#8-웹-화면)
9. [훈련 관리](#9-훈련-관리)
10. [평가](#10-평가)
11. [한계](#11-한계)

## 1. 주제

| 항목 | 내용 |
|---|---|
| 사용자 | 골프 입문 0~3개월 오른손잡이, 본인 포함 2~10명 |
| 문제 | 레슨 없는 날에는 내 자세가 맞는지 확인할 방법이 없고, 유튜브·블로그는 일반론이라 출처마다 말이 다름 |
| 해결 | 사진·영상 → 관절 각도 측정 → 교육용 기준값과 비교 → 근거 문서로 교정 방법 안내. 질문은 근거가 있을 때만 답함 |

## 2. 주요 기능

- **자세 판정**: 측면 어드레스 사진(척추 앞기울기·무릎 각도), 정면 스윙 영상·GIF(백스윙 정점의 왼팔 각도). 영상은 프레임별 각도 그래프가 재생 위치와 함께 움직입니다.
- **골프 질문**: 지식 문서 25개(기술·규칙·용어·코스·장비·연습·역사 등)를 검색하고, 최신 정보는 MCP 웹 검색으로 찾습니다.
- **가드레일**: 의학 상담·구매/가격·개인정보·프롬프트 인젝션은 차단합니다. 골프와 무관한 질문은 검색 없이 no_answer 로 끝냅니다.
- **기억**
  - 단기: 같은 대화방에서는 앞 대화와 직전 판정을 이어서 답합니다.
  - 장기: 로그인한 계정의 판정 기록은 대화방·기기를 넘어 남겨 지난 판정과 비교합니다.
  - 로그인하지 않아도(게스트) 질문과 판정은 쓸 수 있습니다.
- **계정**: 아이디·비밀번호 가입 또는 **Google 계정 로그인**. Google 로 처음 로그인하면 이메일 앞부분으로 아이디를 자동으로 만듭니다(겹치면 뒤에 숫자). 관리자는 모든 사용자의 코칭·훈련을 읽기 전용으로 보고 지웁니다.
- **훈련 관리**: 상단 '훈련' 화면. 자세한 내용은 [9장](#9-훈련-관리).
  - 골프 문서에서 정리한 훈련 라이브러리 25개(8개 분야)
  - 월간 달력에서 날짜 배치·요일 루틴을 잡고 완료를 체크하며, 이번 주·이번 달 달성률을 봅니다.
  - 판정에서 고칠 점이 나오면 규칙으로 2주 플랜(월·수·금)을 추천합니다. 판정과 연결된 항목은 각도 변화 그래프를 보여 줍니다.
  - "요즘 연습 어때?"에는 코드가 집계한 연습 요약을 근거로 답합니다.
- **웹 화면**: 질문·사진·영상 첨부, 샘플 사진·영상 선택, 판정 막대·각도 그래프·근거·처리 단계 표시, 아키텍처 다이어그램(모듈을 누르면 실제 코드). 모바일(760px 이하)도 지원합니다.

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
   - '지난번 판정' · '요즘 연습' 질문 ────▶ 장기 기억 · 연습 요약을 근거로 바로 coach
   - 자세 인식 실패 · 웹에서도 근거 없음 ──▶ no_answer (고정 문구, LLM 호출 없음)
```

- LLM(생성) 호출은 **coach 한 곳**뿐입니다. sub-agent 는 도구만 쓰고, 문서 검색의 의미 검색에만 Titan 임베딩을 씁니다.
- Agent교육 11장과 `/architecture` 에서 같은 구조를 다이어그램으로 볼 수 있습니다. 모듈을 누르면 해당 함수의 실제 코드가 줄 번호와 함께 열립니다.

### 폴더 구조

소스는 `src/` 아래 **도메인(기능)별 폴더**로 나뉩니다. 각 도메인 폴더 안에서 `agent.py` 는 sub-agent, `prompts.py` 는 LLM 프롬프트, `schemas.py` 는 입출력 타입입니다. 자세한 배치 규칙은 [CLAUDE.md](CLAUDE.md) 에 있습니다.

```
src/
├── coach/       메인 그래프(LangGraph): guard → supervisor → sub-agent → coach, 단기 기억(체크포인터)
│                graph.py · prompts.py(코칭 시스템 프롬프트) · schemas.py(AgentState, /query 요청·응답)
├── guard/       규칙 기반 가드레일 (같은 질문은 항상 같은 결과)             rules.py · schemas.py
├── swing/       swing_agent: 각도 계산·문제 코드 판정(순수 함수), MediaPipe 추정기
│                agent.py · pose.py · estimator.py · schemas.py
├── knowledge/   knowledge_agent: BM25(kiwipiepy 형태소) · Titan/Chroma · 하이브리드 검색
│                agent.py · retriever.py · schemas.py
├── web_info/    info_agent: MCP 클라이언트 → stdio MCP 서버 golf_web_search → DuckDuckGo(ddgs)
│                agent.py · search.py · mcp_server.py · schemas.py
├── memory/      장기 기억: 계정별 판정 기록(코드·각도), LangGraph Store       store.py
├── history/     코칭 내역: 계정별 대화 목록, 대화방(thread_id) 소유권           store.py
├── training/    훈련 관리: 라이브러리·항목·기록·일정·달성률·플랜 제안·연습 요약 (집계는 모두 코드)
│                service.py · schemas.py
├── accounts/    계정: 가입·로그인·세션, Google 로그인, 캐릭터 아이콘
│                auth.py · google.py · avatar.py · schemas.py
├── evaluation/  자체 평가: 코드 채점 · LLM 심사(고정 루브릭) · 검색 보정
│                evaluate.py · calibrate.py · judge.py · prompts.py · schemas.py
├── api/         FastAPI 진입점 · 아키텍처 코드 보기 · 웹 화면       app.py · arch_code.py · static/
└── shared/      공통: 설정값·고정 문구·기준값, Bedrock LLM 클라이언트(모델 대체 순서), 가짜 모델, trace 유틸
                 config.py · llm.py · fakes.py · trace.py

data/
├── docs/                   교육용 골프 지식 문서 25개
├── training_library.json   훈련 라이브러리 25개
├── calibration.json        검색 기준값 보정 질문 50개 (평가 세트와 별개)
├── fixtures/               채점용 더미 관절 좌표 7개, 가짜 웹 검색 결과
└── test_photos/ · test_videos/   시험용 실제 사진 7장·영상 4개 (출처·라이선스는 각 폴더 SOURCES.md)

tests/          단위·API 테스트 (실제 API 호출 없음)
evaluation/     평가 세트 · 회차별 결과 · 보고서
cloudflare/     배포용 Worker (정적 파일 외 요청을 백엔드로 전달)
```

### 스택

- **고정 스택**: Bedrock Claude Sonnet 4.5 (`us.anthropic.claude-sonnet-4-5-20250929-v1:0`) · Titan v2 임베딩 · `us-east-1` · Chroma(`./chroma_db`, `sds_policies`) · SqliteSaver(`checkpoints.sqlite`)
- **모델 대체**: 고른 모델이 사용량 한도·일시 장애로 실패하면 허용 목록의 다음 모델(Sonnet 4.6, Haiku 4.5, Amazon Nova 등)로 넘어가 중단 없이 답합니다. 실제로 답한 모델은 응답의 `model` 에 남습니다.
- **그 밖**: LangGraph, FastAPI, MediaPipe·OpenCV(자세), SqliteStore(장기 기억), MCP(웹 검색), SQLite(계정·코칭 내역·훈련, `accounts.sqlite`)

### 문제 코드와 교육용 기준값

| 코드 | 입력 | 측정 | 기준(교육용) | 근거 문서 |
|---|---|---|---|---|
| `spine_tilt_out_of_range` | 측면 어드레스 사진 | 척추 앞기울기 | 30~45° | address.md |
| `knee_flex_out_of_range` | 측면 어드레스 사진 | 무릎 각도 | 145~165° | address.md |
| `bent_lead_arm` | 정면 스윙 영상·GIF | 백스윙 정점 왼팔 각도 | 160° 이상 | backswing.md |

## 4. 핵심 의사결정 2가지

1. **스윙 판정은 LLM이 아니라 결정적인 각도 계산이 한다.**
   - 포즈 추정으로 관절 좌표를 얻고, 순수 함수로 각도를 계산해 문제 코드를 냅니다. LLM은 판정 결과를 받아 설명 문장만 씁니다.
   - 같은 입력에는 항상 같은 판정이 나오고, 판정 근거(측정 각도·기준값·근거 문서)를 그대로 보여 줄 수 있습니다.
   - 고정 모델이 영상을 직접 받지 못한다는 제약도 이 구조로 해결했습니다.

2. **라우팅·가드레일은 규칙으로, 외부 부품은 모두 주입 가능하게 만들어 실제 API 없이 채점한다.**
   - supervisor 라우팅과 가드레일은 규칙 기반이라 같은 질문은 항상 같은 경로를 탑니다.
   - LLM·검색기·웹 검색기·포즈 추정기·체크포인터는 생성자 인자로 받습니다. 자동 채점은 가짜 모델 + BM25 + 가짜 웹 검색기 + 더미 관절 좌표로 돕니다.
   - BM25도 실제 문서를 검색하므로 '근거가 없으면 모른다고 답하기'까지 오프라인에서 검증됩니다.
   - 근거 판단 기준값(점수 2.5 이상 + 질문 단어의 IDF 가중 문서 포함 비율 0.5 이상, 라이브 Chroma 관련도 0.2 이상)은 보정 질문 50개([data/calibration.json](data/calibration.json))로 정했습니다.

## 5. 시작하기

### 설치

```bash
git clone https://github.com/shkim0001/swingkid.git
cd swingkid
python -m venv .venv && source .venv/Scripts/activate   # macOS·Linux: source .venv/bin/activate
pip install -r requirements.txt
```

### 환경 변수 (`.env`)

프로젝트 폴더에 `.env` 를 만듭니다. 저장소에는 올리지 않습니다(`.gitignore`). 오프라인 모드는 아무것도 없어도 됩니다.

| 변수 | 필요할 때 | 설명 |
|---|---|---|
| `AWS_ACCESS_KEY_ID` · `AWS_SECRET_ACCESS_KEY` · `AWS_DEFAULT_REGION=us-east-1` | 라이브 모드 | Bedrock(Claude·Titan) 호출 |
| `ADMIN_PASSWORD` | 관리자를 쓸 때 | 8자 이상. 서버가 켜질 때 `admin` 계정을 이 비밀번호로 만들거나 맞춤 |
| `GOOGLE_CLIENT_ID` · `GOOGLE_CLIENT_SECRET` | Google 로그인을 쓸 때 | 둘 다 있어야 화면에 'Google로 계속하기' 버튼이 보임 |
| `GOOGLE_REDIRECT_URI` | 선택 | 콜백 주소를 고정할 때. 없으면 브라우저가 보는 주소로 `…/auth/google/callback` 을 만듦 |
| `LANGSMITH_TRACING` · `LANGSMITH_API_KEY` · `LANGSMITH_PROJECT` | 선택 | LangSmith 추적. 한도를 넘으면 경고만 찍히고 동작에는 영향 없음 |

### 실행

```bash
./run.sh test        # 단위·API 테스트 (실제 API 호출 없음)
./run.sh eval        # 평가 세트 오프라인 자체 평가
./run.sh offline     # 가짜 모델로 서버 실행 (AWS 불필요)
./run.sh live        # 실제 Bedrock · Titan/Chroma · MCP 웹 검색 · MediaPipe
GOLF_DEMO=1 ./run.sh live   # 라이브 + 더미 관절 좌표 데모 경로
```

Windows PowerShell에서는 아래처럼 실행합니다.

```powershell
python -m pytest -q
python -m src.evaluation.evaluate --detail
$env:GOLF_MODE="offline"; python -m uvicorn src.api.app:app --port 8000   # 라이브는 "live"
```

서버를 띄운 뒤 http://localhost:8000 을 엽니다.

라이브로 처음 실행하면 아래 파일들이 자동으로 생깁니다. 모두 저장소에는 없습니다.
- `chroma_db/`: Chroma 검색 색인. 문서로 만듭니다.
- `data/models/`: 포즈 모델 파일(약 9MB). 처음 사진·영상을 분석할 때 공식 주소에서 내려받습니다.
- `checkpoints.sqlite`, `memory.sqlite`, `accounts.sqlite`: 대화 기록, 장기 기억, 계정·코칭 내역·훈련

### Google 로그인 설정

1. [Google Cloud Console](https://console.cloud.google.com) 에서 프로젝트를 만들고, **Google 인증 플랫폼**(예전 이름: API 및 서비스 → OAuth 동의 화면)으로 갑니다.
2. 동의 화면을 설정합니다. **대상: 외부**로 두고 **앱 게시**를 해야 아무 Google 계정이나 로그인할 수 있습니다. 이 서비스는 `openid email` 만 요청합니다.
3. **클라이언트 → 클라이언트 만들기 → 웹 애플리케이션**을 고르고, 승인된 리디렉션 URI를 추가합니다.
   ```
   http://localhost:8000/auth/google/callback
   http://127.0.0.1:8000/auth/google/callback
   https://<배포 도메인>/auth/google/callback
   ```
4. 클라이언트 ID와 보안 비밀번호를 `.env` 의 `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` 에 넣고 서버를 다시 켭니다.

## 6. 서버 올리기: 로컬 / Cloudflare

| | A. 로컬 | B. Cloudflare 배포 |
|---|---|---|
| 접속 주소 | `http://localhost:8000` (같은 Wi-Fi 기기는 `http://<PC IP>:8000`) | `https://<배포 도메인>` (어디서나) |
| 필요한 것 | Python, `.env` | A + Cloudflare 계정, `cloudflared`, `wrangler` |
| 화면·API | FastAPI 가 모두 내려줌 | 화면은 Cloudflare, API 는 Worker → 터널 → 내 PC |
| Google 로그인 | `localhost` 에서만 (사설 IP 주소는 Google 이 리디렉션 주소로 받지 않음) | 배포 도메인에서 |
| `cloudflare/`, `wrangler.jsonc` | 쓰지 않음 | 사용 |

### A. 로컬에서만 쓰기 (Cloudflare 없이)

Cloudflare·Worker·터널은 필요 없습니다. FastAPI 서버 하나가 화면(`src/api/static`)과 API 를 모두 내려줍니다.

1. `.env` 를 준비합니다([환경 변수](#환경-변수-env)). 라이브는 AWS 키가 필요하고, 오프라인은 없어도 됩니다.
2. 서버를 띄웁니다.
   ```bash
   ./run.sh live                 # 실제 Bedrock (오프라인 시연은 ./run.sh offline)
   ```
   ```powershell
   # Windows PowerShell
   $env:GOLF_MODE="live"; python -m uvicorn src.api.app:app --port 8000
   ```
3. 브라우저에서 http://localhost:8000 을 엽니다. API 문서는 http://localhost:8000/docs 입니다.
4. **Google 로그인**은 리디렉션 URI 에 `http://localhost:8000/auth/google/callback` 과 `http://127.0.0.1:8000/auth/google/callback` 만 등록하면 됩니다. `GOOGLE_REDIRECT_URI` 는 비워 둡니다(접속한 주소로 자동으로 만듦).
5. **같은 Wi-Fi 의 휴대폰 등에서 보려면** 모든 네트워크 주소에서 받도록 띄웁니다.
   ```bash
   GOLF_MODE=live python -m uvicorn src.api.app:app --host 0.0.0.0 --port 8000
   ```
   - 휴대폰에서 `http://<PC 의 IP>:8000` 으로 엽니다. IP 는 Windows `ipconfig`, macOS `ipconfig getifaddr en0` 으로 확인합니다.
   - 처음이면 Windows 방화벽에서 Python 허용을 물어봅니다.
   - 이 주소에서는 Google 로그인이 되지 않습니다(Google 이 `192.168.…` 같은 사설 IP 를 리디렉션 주소로 받지 않음). 아이디·비밀번호로 로그인하거나, 바깥에서도 Google 로그인을 쓰려면 B 방식을 씁니다.
6. 끄려면 서버 창에서 `Ctrl+C` 를 누릅니다. 데이터(`accounts.sqlite`, `memory.sqlite`, `checkpoints.sqlite`, `chroma_db/`)는 프로젝트 폴더에 남아 다음 실행에 이어집니다.

> `--reload` 를 붙이면 코드를 고칠 때마다 서버가 다시 켜집니다(개발용). `.env` 를 바꿨으면 서버를 다시 켜야 반영됩니다.

### B. Cloudflare 로 배포하기

```
브라우저 ──▶ Cloudflare
               ├─ 정적 파일(/, /architecture, 아이콘 …) ── src/api/static 을 Cloudflare 가 직접 내려줌
               └─ 그 밖의 요청(/query, /auth/*, /training/* …) ── Worker(cloudflare/worker.js)
                                                                  └─▶ Cloudflare Tunnel ──▶ 내 PC 의 FastAPI(:8000)
```

1. 내 PC에서 라이브 서버를 띄웁니다. `./run.sh live`
2. Cloudflare Tunnel 로 `http://localhost:8000` 을 외부 주소에 연결합니다(예: `cloudflared tunnel --url http://localhost:8000`).
3. 터널 주소를 Worker 비밀값으로 넣습니다. `npx wrangler secret put BACKEND_URL`
   - 이 주소는 [wrangler.jsonc](wrangler.jsonc) 가 아니라 Cloudflare Secret 에 둡니다.
   - 터널 주소가 바뀌면 이 명령만 다시 하면 되고, 재배포는 필요 없습니다.
4. Worker 를 배포합니다. `npx wrangler deploy`
   - Worker 가 원래 도메인(`X-Forwarded-Host`)을 백엔드에 넘깁니다. 그래서 Google 로그인 콜백 주소가 배포 도메인으로 만들어집니다.
   - 이 배포 도메인의 콜백 주소를 Google Console 리디렉션 URI 에 등록해야 합니다.

## 7. API

표준 규약(`question` → `answer`, `contexts`, `trace`)을 그대로 따르고, 이 서비스에 필요한 필드를 덧붙였습니다. 서버를 띄우면 http://localhost:8000/docs 에서 전체 스키마를 볼 수 있습니다.

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
    {"step": "memory", "input": "account:골프키드", "output": "이번 판정 기록 저장"}
  ],
  "status": "answered",
  "issue_codes": ["spine_tilt_out_of_range"],
  "sources": ["address.md"],
  "agents": ["swing_agent"],
  "angle_series": null,
  "model": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
  "training_proposal": {"id": "…", "issue_codes": ["spine_tilt_out_of_range"], "drills": [{"key": "…", "title": "…", "unit": "회", "target": 10, "minutes": 10, "location": "집", "doc_id": "address.md"}], "weekdays": [0, 2, 4], "start": "…", "end": "…", "sessions": 6}
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
| `model` | 추가 응답 | 실제로 답변을 쓴 모델 (대체 경로로 바뀌었으면 그 모델) |
| `training_proposal` | 추가 응답 | 로그인했고 문제 코드가 나왔을 때 만든 2주 연습 플랜 제안. 같은 문제로 진행 중인 제안·플랜이 있으면 `null` |

- 장기 기억의 주인은 요청 본문이 아니라 로그인 세션 쿠키(`swingkid_session`)로 정합니다. 로그인하지 않았으면 장기 기억을 읽거나 쓰지 않습니다.
- 오류 응답
  - 422: 모르는 필드(예: 채점용 `pose_fixture`, 예전 `user_id`), base64 오류, 사진과 영상 동시 전송, 허용 목록 밖 모델
  - 413: 10MB 초과
  - 429: Bedrock 사용량 한도
  - 403: 남의 대화방

### 그 밖의 경로

| 경로 | 설명 |
|---|---|
| `GET /` · `GET /architecture` | 웹 화면 · 아키텍처 다이어그램(크게 보기) |
| `GET /architecture/code/{module}` | 다이어그램 모듈의 중요 코드(실제 소스에서 함수 단위로) |
| `GET /models` · `GET /health` | 코칭 모델 목록 · 상태 |
| `GET /samples/media` · `GET /samples/media/{name}` | 샘플 사진·영상 목록 · 파일 |
| `POST /auth/signup` · `POST /auth/login` · `POST /auth/logout` · `GET /auth/me` | 가입(바로 로그인) · 로그인 · 로그아웃 · 지금 로그인한 아이디. 본문 `{username, password}`, 세션은 HttpOnly 쿠키(30일) |
| `GET /auth/providers` | 쓸 수 있는 로그인 방법 `{password, google}` (Google 은 `.env` 설정이 있을 때만 `true`) |
| `GET /auth/google/login` · `GET /auth/google/callback` | Google 동의 화면으로 이동 · Google 이 돌려보내는 곳(처음이면 계정 생성 → 세션 쿠키 → `/`). 실패하면 `/?login_error=google` 또는 `google_cancel` |
| `GET /auth/avatar.svg` | 내 캐릭터 아이콘(로그인하지 않으면 401) |
| `GET /memory` · `DELETE /memory` | 내 장기 기억 보기 · 지우기 (로그인하지 않으면 401) |
| `GET /threads` · `PUT·DELETE /threads/{thread_id}` · `DELETE /threads` | 내 코칭 내역 보기 · 하나 저장/지우기 · 모두 지우기 (로그인하지 않으면 401, 남의 대화방이면 403) |
| `GET /training/library` | 훈련 라이브러리(게스트도 볼 수 있음) |
| `GET /training` · `GET /training/calendar?year=&month=` | 내 항목·집계·최근 기록·오늘 계획·추천 제안 · 월 달력과 이번 주/달 달성률 |
| `POST /training/drills` · `POST /training/drills/from-library` · `DELETE /training/drills/{id}` | 직접 만들기 · 라이브러리에서 넣기 · 목록에서 내리기(기록은 남음) |
| `POST /training/drills/{id}/logs` · `DELETE /training/logs/{id}` · `GET /training/drills/{id}/trend` | 기록(날짜·횟수·메모) · 지우기 · 판정 각도 변화 |
| `POST·DELETE /training/plans` · `POST·DELETE /training/routines` · `POST /training/routines/{id}/skip` | 날짜 배치 · 요일 루틴 · 루틴 하루 빼기 |
| `POST /training/proposals/{id}/accept` · `.../dismiss` | 판정 추천 2주 플랜 받기(루틴 생성) · 닫기 |
| `GET /admin/threads` · `DELETE /admin/threads/{thread_id}` · `GET /admin/avatars/{username}.svg` | [관리자] 모든 계정의 코칭 보기 · 지우기 · 주인 캐릭터 (관리자가 아니면 403) |
| `GET /admin/training` · `GET /admin/training/{username}` · `DELETE /admin/training/{drills,logs,routines}/{id}` | [관리자] 사용자 훈련 현황 · 한 사용자의 달력·항목·기록 · 지우기 |
| `GET /demo/samples` · `POST /demo/fixture` | 추천 질문 목록 · 더미 관절 좌표로 판정(오프라인, 또는 라이브에서 `GOLF_DEMO=1`일 때만) |

**입력 전제**
- 사진은 **측면**(뒤에서 비구 방향으로) 어드레스 자세, 영상은 **정면** 스윙만 판정합니다. **오른손잡이** 기준이며, 이 조건은 실행 중에 검사하지 않습니다.
- 사람이 없거나 필요한 관절이 잘 안 보이면(인식 신뢰도 0.5 미만) 판정하지 않고 다시 찍어 달라고 안내합니다.

## 8. 웹 화면

- **첫 화면**: 주소 `/` 로 들어오면 나오는 소개 화면입니다(상단 바 없음). 자체 머리줄(로고 · 코칭 · 훈련 · Agent 교육), 한 줄 문구와 [코칭 시작하기](`#coach`) · [훈련 달력](`#training`) 버튼, 빛 번짐 위에 스윙 궤적을 그린 추상 이미지, 핵심 기능 3가지. SVG 로 그려 다크 모드에 맞춰 바뀝니다. 코칭 · 훈련 화면에서 상단 로고를 누르면 돌아옵니다.
- **배치**
  - 왼쪽 사이드바: 로그인, 장기 기억, 오늘의 훈련, 판정 기준
  - 가운데: 대화창
  - 입력창 바로 위 가로 칩 줄은 추천 질문(`test_queries.csv`)이며, 누르면 입력창에 채워집니다.
- **로그인**
  - 아이디(띄어쓰기 없이 한글·영문·숫자·`-`·`_` 2~32자)와 비밀번호(8자 이상)로 가입·로그인합니다. 서버에 Google 설정이 있으면 **Google로 계속하기**로 로그인할 수도 있습니다.
  - Google 로 처음 로그인하면 이메일 앞부분으로 아이디를 만듭니다. 예: `kim.golf@gmail.com` → `kim_golf`. 아이디가 이미 있으면 `kim_golf2` 처럼 숫자를 붙입니다.
  - Google 로 만든 계정은 Google 로만 로그인합니다(비밀번호 로그인 불가).
  - 가입할 때 계정마다 무작위 캐릭터 아이콘이 정해져 프로필과 코칭 카드에 보입니다.
  - 게스트는 질문·판정만 되고 기록은 남지 않습니다.
- **코칭**
  - 가운데 카드가 지금 대화(thread_id)입니다. 왼쪽 '코칭 목록'에 내 코칭이 최근순으로 쌓이고, 누르면 가운데에서 열립니다.
  - 로그인하면 코칭 내역이 계정별로 서버에 저장되어 자기 것만 보이고, 다른 기기에서도 이어집니다.
  - 게스트 내역은 이 브라우저(localStorage)에만 남습니다.
- **첨부**
  - "사진·영상" 버튼이나 끌어다 놓기로 첨부합니다. '사진·영상 샘플'에서 시험용 파일을 고를 수도 있습니다.
  - 영상·GIF는 대화 카드에서 자동 재생·반복됩니다.
  - 원본은 브라우저(IndexedDB)에만 저장되어 새로고침해도 재생되지만, 다른 기기에서는 재생되지 않습니다.
- **답변**
  - 상태·문제 코드·관절 각도 막대(초록 구간이 교육용 기준)·근거·처리 단계·응답 JSON을 보여 줍니다.
  - 영상 판정은 '왼팔 각도 변화' 그래프가 재생 위치에 맞춰 움직이고, 그래프를 누르면 그 장면으로 이동합니다.
- **훈련**: 상단 '훈련'(주소 `#training`).
  - 이번 주·이번 달 달성률과 연속 연습일을 봅니다.
  - 판정 추천 플랜을 받거나 닫습니다.
  - 월간 달력(점: 완료·오늘·지남·예정·계획 밖 연습)에서 고른 날의 계획을 완료 체크하고, 기록·메모를 남기고, 빼거나 넣습니다.
  - 훈련 라이브러리(분야·장소 필터)에서 항목을 일정에 넣고, 항목 상세에서 기록과 각도 변화 그래프를 봅니다.
- **관리자**
  - `.env` 의 `ADMIN_PASSWORD` 로 생기는 `admin` 계정입니다. 화면에서는 `admin` 으로 가입할 수 없고, Google 로그인도 `admin` 아이디를 피합니다.
  - 다른 계정의 코칭과 훈련을 읽기 전용으로 열고 지울 수 있습니다. 이어 쓰지는 못합니다.
- **코칭 모델**
  - 상단에서 Bedrock 모델 10개(Claude Sonnet 4.5·4.6, Haiku 4.5, Amazon Nova) 중 고릅니다.
  - 답변 작성에만 쓰이고, 검색·판정은 같습니다. 오프라인에서는 항상 가짜 모델이 답합니다.
- **모바일(760px 이하)**
  - 상단 바는 ☰ · 로고 · 내 캐릭터만 둡니다. 프로필·코칭 목록·판정 기준·모델 선택은 ☰ 로 여는 왼쪽 서랍에 있습니다.
  - 입력창은 한 줄([샘플] [📷] [입력칸] [↑])입니다.
- **Agent 교육**: 상단 바 오른쪽 끝 'Agent 교육' 버튼을 누르면 화면 전체를 덮는 팝업으로 열립니다(주소 `#edu-<개념>-<장>`, ✕ · Esc 로 닫기). SwingKid 를 만든 SDS AX Agent 교육을 **16:9 발표 슬라이드 51장**으로 정리했습니다. 12장은 미니 PJT 가이드(일정 · 산출물 규약 · 템플릿 · 12개 패턴 · 제출과 채점)입니다. 11개 개념마다 흐름도 · 번호 매긴 설명 · 용어 · 핵심 코드 · 바로 써 볼 도구(archify · graphify · LangGraph Studio · LangSmith · Ouroboros 등)가 있고, ←/→ · Space 로 넘기고 F 로 전체 화면 발표합니다. 폰트는 도현체 · 주아체 · D2Coding. 내용은 [src/api/static/edu.js](src/api/static/edu.js) 의 데이터만 고치면 됩니다.
- **아키텍처**: 'SwingKid는 이렇게 동작해요' 다이어그램은 Agent 교육 11장(실전 적용)에 있고, `/architecture` 에서 크게 볼 수 있습니다. 보라색이 LLM·모델 호출이며, 모듈을 누르면 실제 코드가 열립니다.

## 9. 훈련 관리

판정을 받은 뒤 **"무엇을, 언제, 얼마나 연습했고, 나아졌는지"**까지 이어 주는 기능입니다. 상단 '훈련'(주소 `#training`) 화면에서 쓰고, 기록 · 일정은 로그인한 사용자만 저장됩니다(게스트는 라이브러리만 둘러볼 수 있음).
추천 · 집계 · 달성률 · 각도 변화는 **모두 코드가 계산**하고, LLM 은 코칭 답변에서 그 결과를 설명만 합니다.

```
사진·영상 판정 ──▶ 문제 코드 (예: bent_lead_arm)
                      │ 규칙으로 매칭 (LLM 없음)
                      ▼
               2주 연습 플랜 제안 (월·수·금) ──받기──▶ 요일 루틴 → 달력에 계획
                                                           │
   라이브러리 · 직접 만든 항목 ──날짜에 넣기 / 요일 루틴──▶ 계획 ──기록하면──▶ 완료
                                                           │
                      달성률 · 연속 연습일 · 각도 변화 ◀──┘ ──▶ 코칭 답변의 근거("요즘 연습 어때?")
```

### 훈련 라이브러리

골프 지식 문서 25개의 '연습 방법' · 교정 내용에서 뽑은 **25개 항목, 8개 분야**입니다([data/training_library.json](data/training_library.json), 교육용 더미 데이터).

| 분야 | 항목 수 | | 분야 | 항목 수 |
|---|---|---|---|---|
| 그립 | 3 | | 다운스윙 · 임팩트 | 6 |
| 어드레스 | 3 | | 숏게임 | 2 |
| 백스윙 | 3 | | 퍼팅 | 3 |
| 몸풀기 | 2 | | 루틴 · 연습 구성 | 3 |

- 항목마다 **하는 법 · 장소(집 · 연습장 · 어디서나) · 소요 시간 · 준비물 · 난이도(입문 · 기본 · 도전) · 1회 목표 · 근거 문서**가 있습니다.
- 화면에서 분야 · 장소로 거르고, 상세에서 바로 일정에 넣습니다.
- 라이브러리에 없는 연습은 직접 만듭니다(이름 40자 이내, 단위 회 · 분 · 개, 1회 목표).

### 일정 잡기 세 가지

| 방법 | 설명 |
|---|---|
| 날짜에 직접 넣기 | 달력에서 날짜를 고르고 라이브러리 · 내 항목을 넣습니다. 같은 날 같은 항목은 한 번만 |
| 요일 루틴 | 항목 · 요일 · 기간(시작 ~ 끝, 최대 1년)을 정하면 기간 안의 해당 요일마다 계획이 생깁니다. 루틴 중 하루만 빼기도 됩니다 |
| 판정 기반 추천 플랜 | 사진 · 영상 판정에서 문제 코드가 나오면 **오늘부터 2주, 월 · 수 · 금** 플랜을 제안합니다. 받으면 요일 루틴으로 달력에 들어가고, 닫으면 사라집니다 |

판정 문제 코드 → 추천 연습 항목 (규칙, LLM 없음)

| 문제 코드 | 추천 연습 | 근거 문서 |
|---|---|---|
| `spine_tilt_out_of_range` | 클럽을 엉덩이 옆에 대고 셋업 반복 | address.md |
| `knee_flex_out_of_range` | 거울 · 휴대폰 옆에 두고 셋업 반복(무릎 각도) | address.md |
| `bent_lead_arm` | 백스윙 정점에서 멈추고 왼팔 확인 · 수건을 겨드랑이에 끼우고 하프 스윙 | backswing.md |

- 같은 문제로 받을지 기다리는 제안이나 진행 중인 플랜이 있으면 **다시 제안하지 않습니다**(같은 판정을 여러 번 받아도 플랜이 쌓이지 않음).
- 제안은 판정 응답의 `training_proposal` 필드로 오고, 코칭 답변 아래에 '플랜 받기' 안내가 붙습니다.

### 기록 · 완료 · 달성률

- **기록**: 항목마다 날짜 · 횟수 · 메모(300자). 하루에 여러 번 남길 수 있고, 계획에 없던 연습도 기록됩니다.
- **완료 판정**: 계획한 날 그 항목의 기록이 있으면 완료입니다. 달력의 날마다 **완료 · 오늘 · 지남(기록 없음) · 예정**과 계획 밖 연습을 점으로 보여 줍니다.
- **달성률**: 이번 주 · 이번 달 = 완료 ÷ **오늘까지** 계획(앞으로의 계획은 분모에 넣지 않음).
- **주간 목표 · 연속 연습일**: 일주일에 연습한 날 3일이 목표이고, 연속 연습일과 항목별 이번 주 · 누적 횟수를 함께 셉니다.
- **각도 변화 그래프**: 판정과 연결된 항목(척추 · 무릎 · 왼팔)은 그 각도의 판정 기록을 날짜순으로 그리고, 교육용 기준 구간을 함께 표시합니다. 연습하면서 실제로 각도가 기준 안으로 들어오는지 봅니다.

### 코칭과 연결

- **"요즘 연습 어때?"** · "이번 주 훈련 기록" · "달성률" 같은 연습 현황 질문은 supervisor 가 코드가 만든 **연습 요약**(이번 주 연습일 · 주간 목표 · 연속일 · 계획 달성 · 놓친 계획 · 항목별 횟수 · 최근 기록)을 근거로 코칭에 넘깁니다. 골프 단어가 없어도 이 서비스의 훈련 기록을 묻는 질문이라 막지 않습니다.
- "퍼팅 연습 방법"처럼 방법을 묻는 질문은 연습 현황이 아니라 골프 지식 문서가 답합니다.
- 새 판정 때도 연습 요약을 함께 넘겨, "그동안 왼팔 연습을 했는데 정점 각도가 좋아졌어요"처럼 짚을 수 있습니다. 기록에 없는 연습 · 날짜는 지어내지 않습니다.
- 코칭 화면 왼쪽 **'오늘의 훈련'** 카드에서 오늘 계획을 바로 체크하고 훈련 화면으로 넘어갈 수 있습니다.

### 관리자

`admin` 은 '사용자 훈련' 탭에서 모든 사용자의 달력 · 항목 · 기록 · 달성률을 읽기 전용으로 보고, 항목 · 기록 · 루틴을 지울 수 있습니다.

### 저장 · 코드 위치

- 저장: `accounts.sqlite`(라이브) — 항목 · 기록 · 날짜 계획 · 요일 루틴 · 루틴에서 뺀 날 · 추천 제안 표. 오프라인 모드는 메모리에만
- 코드: [src/training/service.py](src/training/service.py)(라이브러리 · 저장소 · 달력 · 달성률 · 추천 · 연습 요약), [src/training/schemas.py](src/training/schemas.py)(요청 본문), API 경로는 [7장](#그-밖의-경로)의 `/training/*`
- 테스트: `tests/test_training*.py` (저장소 규칙 · 라이브러리 검증 · 일정과 달력 · 추천 플랜 · API · 코칭 연결 · 각도 변화)

## 10. 평가

계획은 [evaluation/EVAL_PLAN.md](evaluation/EVAL_PLAN.md) 에 있습니다. 회차별 보고서는 [1차(기준선 v1)](evaluation/round1_report.md) · [2차(v2)](evaluation/round2_report.md) · [3차(v3)](evaluation/round3_report.md) · [4차(v4)](evaluation/round4_report.md) 입니다. 평가는 세 겹으로 봅니다.

| 겹 | 무엇을 | 명령 |
|---|---|---|
| A. 코드 채점 | status·issue_codes·sources·agents·처리 단계·고정 문구 | `python -m src.evaluation.evaluate` (오프라인, 실제 API 없음) |
| B. LLM 심사 | 기계로 못 보는 기대 특성·금지 항목, 근거 밖 내용, 질문 적합성 | `python -m src.evaluation.evaluate --live --judge` |
| C. 검색 진단 | 기대 문서 재현율·1위 비율·MRR | 평가 결과에 함께, `python -m src.evaluation.calibrate [--live]` |

- LLM 심사는 judge 모델(Sonnet 4.5)·temperature 0·루브릭을 고정합니다. 같은 답을 두 번 채점해 다르면 세 번째로 다수결하고, 낮은 점수·위반·판정 불일치는 사람 검토 대상으로 모읍니다.
- 결과를 저장하고(`--save --label v2`) 이전 결과와 비교합니다(`--compare evaluation/results/<이전>.json`). 고쳐진 문항과 회귀(PASS → FAIL)를 함께 보여 줍니다.
- 평가셋 조각 앞에 `오프라인:` / `라이브:` 를 붙이면 그 모드에서만 확인합니다(실행마다 바뀌는 웹 검색 결과 대응).
- 평가는 `--strict-model` 로 코칭 모델 대체 경로를 꺼서 회차 간 모델 조건을 같게 둡니다.

| 회차 (2026-11-12, 라이브) | 코드 채점 | judge 평균 | 정답 | 근거 밖 내용 없음 | 위반 |
|---|---|---|---|---|---|
| v1 기준선 | 19/20 | 4.09 | 73% | 82% | 3 |
| v2 프롬프트·검색 개선 | 18/20 | 4.67 | 92% | 83% | 2 |
| v3 judge 수정·모드별 기대값 | 19/20 | 4.92 | 100% | 100% | 0 |
| v4 웹 검색 대체 경로·가드레일 보강·범위 프롬프트 정리 | 20/20 | 5.00 | 100% | 100% | 0 |

**현재 상태 (2026-11-13)**
- 단위·API 테스트 362 passed. Google 로그인 테스트 28개를 포함하며, 모두 실제 API 호출 없이 돕니다.
- 오프라인 코드 채점 20/20, 보정 질문 50개 오류 0건(재현율 100% · MRR 1.00)입니다.
- 3차까지 남았던 웹 검색 환경 실패(6번)는 검색 엔진 대체 경로로 4차에서 해결했습니다.

**가드레일 스트레스 세트** (`evaluation/guardrail_stress.csv` 18건)
- 구성: 표현을 바꾼 공격 12건(돌려 말한 통증, '뭐 사요'·브랜드 비교, 역할 바꾸기·가짜 SYSTEM 메시지·지시문 노출 요청 등)과 막으면 안 되는 정상 질문 6건
- 결과: 가드 보강 전 8/18(3건은 실제로 답변이 나감) → 보강 후 18/18(라이브도 18/18)
- 실행: `python -m src.evaluation.evaluate --set evaluation/guardrail_stress.csv`

## 11. 한계

- 각도 기준값과 골프 문서는 모두 **교육용 더미 데이터**입니다. 실제 코칭 기준이 아닙니다.
- 2D 관절 좌표라 촬영 각도가 달라지면 측정값이 달라집니다. 영상은 백스윙 정점 근처 각도가 프레임마다 ±8° 정도 흔들립니다. GIF는 프레임을 덜어 내고 색을 줄여, 원본 영상과 판정이 다를 수 있습니다.
- 사진 한 장으로는 스윙 흐름(템포·순서)을 볼 수 없어, 사진은 어드레스 자세만 판정합니다.
- 웹 검색(DuckDuckGo)은 결과가 실행마다 다르고, 가끔 시간 초과·빈 결과가 납니다.
- 계정
  - 비밀번호 변경·찾기, 회원 탈퇴, 로그인 시도 횟수 제한이 없습니다.
  - 이미 있는 비밀번호 계정과 Google 계정을 하나로 합치는 기능이 없습니다. Google 로 로그인하면 별도 계정이 생깁니다.
  - Google 로그인은 서버가 Google 토큰 주소에서 직접 받은 ID 토큰만 믿고 서명 검사를 따로 하지 않습니다(OpenID Connect 허용 방식).
- 예전 닉네임 방식으로 쌓인 판정 기록은 계정으로 옮기지 않습니다.
- 훈련 라이브러리와 추천 플랜(2주·월·수·금)은 교육용 더미 데이터·제안값입니다. 알림·리마인더, 레슨 프로 계정, 플랜 공유, 외부 달력 연동은 없습니다. 연습 현황 질문의 라이브 Bedrock 답변은 아직 심사(LLM judge)하지 않았습니다.
- 골프 규칙 문서는 2023년판 기준입니다. 새 개정판이 나오면 문서를 갱신해야 합니다.
- 배포는 내 PC 백엔드를 Cloudflare Tunnel 로 연결하는 데모 구성입니다. PC가 꺼지면 API 가 멈춥니다.
