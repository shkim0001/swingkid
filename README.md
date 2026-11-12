# 골프 입문 자세 코치 (가칭)

골프를 시작한 지 0~3개월 된 입문자가 **레슨이 없는 날에도 자기 자세를 확인**할 수 있게 돕는 에이전트입니다.
텍스트 질문에는 교육용 골프 문서 범위 안에서만 답하고, 측면 어드레스 사진·정면 스윙 영상은 관절 각도를 숫자로 재서 어디가 기준을 벗어났는지 알려 줍니다.

- 기획: [SERVICE.md](SERVICE.md)
- 개발 현황: [HISTORY.md](HISTORY.md)
- 평가 세트: [evaluation/test_queries.csv](evaluation/test_queries.csv) (20건: positive 8 · negative 4 · edge 5 · guardrail 3)

## 1. 주제

| 항목 | 내용 |
|---|---|
| 사용자 | 골프 입문 0~3개월 오른손잡이, 본인 포함 2~10명 |
| 문제 | 레슨 없는 날에는 내 자세가 맞는지 확인할 방법이 없음 |
| 해결 | 사진·영상 → 관절 각도 측정 → 교육용 기준값과 비교 → 근거 문서로 교정 방법 안내 |

## 2. 설계

```
POST /query
   │
   ▼
 guard ──(의학·구매/가격·인젝션·개인정보)──▶ blocked  (guard_ 코드 1개, 고정 거절 문구)
   ├─(골프와 무관)──▶ no_answer  (검색·웹·LLM 없이 고정 문구, issue_codes=[])
   ▼
 supervisor ──────────┬─▶ swing_agent     사진·영상 → pose(포즈 추정 → 각도 판정)
   ▲  (규칙 기반 라우팅) ├─▶ knowledge_agent 골프 지식 → retrieve(내부 문서 25개)
   │                    └─▶ info_agent      최신 결과·순위·문서 밖 정보 → web_search (MCP 도구 golf_web_search)
   └──── sub-agent 는 결과를 들고 supervisor 로 돌아옴 ────┘
 supervisor 의 다음 결정
   - 근거가 있으면 ─────────────────────▶ coach(LLM 이 근거로 답변 작성) ──▶ answered
   - knowledge_agent 가 근거를 못 찾으면 ─▶ info_agent 로 한 번 더(웹 검색)
   - 자세 인식 실패 ───────────────────▶ no_answer + pose_not_detected (웹으로 넘기지 않음)
   - 웹에서도 근거 없음 ────────────────▶ no_answer (고정 문구, LLM 없음)
```

| 파일 | 역할 |
|---|---|
| [src/agent.py](src/agent.py) | 메인 그래프(LangGraph). 가드 → supervisor(라우팅) → sub-agent → coach(답변 작성) |
| [src/subagents.py](src/subagents.py) | sub-agent 3개: SwingAgent(자세 판정), KnowledgeAgent(문서 RAG), InfoAgent(웹 검색) |
| [src/memory.py](src/memory.py) | 장기 기억. user_id 별 자세 판정 기록(코드·각도)을 LangGraph Store 에 저장(라이브 `memory.sqlite`). 새 판정 때 비교 근거, '지난번 판정' 질문의 근거 |
| [src/web_search.py](src/web_search.py) | 웹 검색 도구. 라이브는 **MCP 클라이언트**(`McpWebSearch`, langchain-mcp-adapters)가 아래 MCP 서버의 `golf_web_search` 도구를 호출. 채점은 고정 결과의 가짜 검색기 |
| [src/mcp_server.py](src/mcp_server.py) | 골프 웹 검색 **MCP 서버**(stdio, FastMCP). `golf_web_search(query, max_results)` 도구를 노출하고 안에서 DuckDuckGo(ddgs, API 키 불필요)로 검색해 `{results:[{url,title,text}], error}` 를 돌려줌 |
| [src/tools.py](src/tools.py) | 도메인 도구. 관절 각도 계산, 문제 코드 판정, 백스윙 정점 검출, MediaPipe 추정기 |
| [src/retriever.py](src/retriever.py) | RAG. BM25(kiwipiepy 형태소) 검색기, Titan/Chroma 검색기, 라이브용 하이브리드(BM25 + Chroma) |
| [src/guard.py](src/guard.py) | 규칙 기반 가드레일 |
| [src/app.py](src/app.py) | FastAPI `POST /query` |
| [src/evaluate.py](src/evaluate.py) | test_queries.csv 오프라인 자체 평가 |
| [src/config.py](src/config.py) | 고정 스택, 기준값, 고정 문구 |
| [data/docs/](data/docs/) | 교육용 더미 골프 문서 25개: 스윙 기술(그립·어드레스·백스윙·다운스윙·드라이버·아이언·우드/유틸리티·숏게임·퍼팅), 구질·미스샷, 경사지, 코스 공략·날씨, 장비, 규칙(기본·구제/벌타, 2023년판), 스코어·핸디캡, 용어, 골프장 구성, 연습법, 준비 운동, 첫 라운드, 멘탈, 에티켓, 역사 |
| [data/fixtures/](data/fixtures/) | 채점용 더미 관절 좌표 7개와 생성 스크립트 |

**고정 스택**: Bedrock Claude Sonnet 4.5 (`us.anthropic.claude-sonnet-4-5-20250929-v1:0`), Titan v2 임베딩, `us-east-1`, Chroma(`./chroma_db`, `sds_policies`), SqliteSaver(`checkpoints.sqlite`). 영상 처리용 MediaPipe·OpenCV는 추가로만 씁니다.

### 문제 코드와 교육용 기준값

| 코드 | 입력 | 측정 | 기준(교육용) | 근거 문서 |
|---|---|---|---|---|
| `spine_tilt_out_of_range` | 측면 어드레스 사진 | 척추 앞기울기 | 30~45° | address.md |
| `knee_flex_out_of_range` | 측면 어드레스 사진 | 무릎 각도 | 145~165° | address.md |
| `bent_lead_arm` | 정면 스윙 영상 | 백스윙 정점 왼팔 각도 | 160° 이상 | backswing.md |

## 3. 핵심 의사결정 2가지

1. **스윙 판정은 LLM이 아니라 결정적인 각도 계산이 한다.**
   포즈 추정으로 관절 좌표를 얻고 순수 함수로 각도를 계산해 문제 코드를 냅니다. LLM은 판정 결과를 받아 설명 문장만 씁니다.
   같은 입력에 항상 같은 판정이 나오고, 판정 근거(측정 각도·기준값·근거 문서)를 그대로 보여 줄 수 있습니다. 고정 모델(Claude Sonnet 4.5)은 영상을 직접 받지 못한다는 제약도 이 구조로 해결했습니다.

2. **실제 API 호출 없이 채점되도록 모든 외부 부품을 주입 가능하게 만들었다.**
   LLM(`llm=None`), 검색기, 포즈 추정기, 체크포인터를 생성자 인자로 받습니다. 자동 채점은 가짜 모델 + BM25 검색 + 더미 관절 좌표 fixture로 돌고, 라이브 시연만 Bedrock·Titan/Chroma·MediaPipe를 씁니다.
   BM25도 실제 문서를 검색하므로 '문서에 근거가 없으면 모른다고 답하기'까지 오프라인에서 검증됩니다. 근거 판단 기준값(최고 점수 2.5 이상 + 질문 단어의 IDF 가중 문서 포함 비율 0.5 이상)은 평가 세트와 별개인 보정용 질문 50개([data/calibration.json](data/calibration.json))로 정했습니다. 최신 결과·순위·시세·장소 추천 질문은 문서에 없는 정보라 검색 전에 근거 없음으로 봅니다. 라이브 Chroma 색인은 시작할 때 문서와 맞춰(바뀐 조각만 다시 임베딩) 문서를 추가해도 따로 지울 필요가 없습니다.

## 4. API

표준 규약(`question` → `answer`, `contexts`, `trace`)을 그대로 따르고, 이 서비스에 필요한 필드를 덧붙였습니다.

```http
POST /query
Content-Type: application/json

{"question": "제 어드레스 자세 봐주세요", "image_base64": "<측면 사진>", "thread_id": "user-1"}
```

```json
{
  "answer": "상체가 많이 숙여졌어요. 척추 앞기울기가 52°로 교육용 기준(30~45°)보다 큽니다…",
  "contexts": [{"doc_id": "address.md", "text": "# 어드레스 자세 …"}],
  "trace": [
    {"step": "guard", "input": "제 어드레스 자세 봐주세요", "output": "통과"},
    {"step": "pose", "input": "photo", "output": "{'issue_codes': ['spine_tilt_out_of_range'], 'spine_tilt': 52.0, 'knee_flex': 155.0}"},
    {"step": "coach", "input": "제 어드레스 자세 봐주세요", "output": "상체가 많이 숙여졌어요…"}
  ],
  "status": "answered",
  "issue_codes": ["spine_tilt_out_of_range"],
  "sources": ["address.md"]
}
```

| 필드 | 구분 | 설명 |
|---|---|---|
| `question` | 표준 | 사용자 질의 (필수) |
| `image_base64` / `video_base64` | 추가 | 측면 어드레스 사진 / 정면 스윙 영상 (선택, 한 번에 하나, 디코딩 후 10MB 이하) |
| `thread_id` | 추가 | 같은 값을 보내면 대화가 이어집니다 (선택) |
| `status` | 추가 | `answered` · `no_answer` · `blocked` |
| `issue_codes` | 추가 | 스윙 문제 코드, `pose_not_detected`, 또는 `guard_` 코드 1개 |
| `sources` | 추가 | `contexts`의 문서 ID 목록 |

오류: 모르는 필드(예: 채점용 `pose_fixture`)·base64 오류·사진과 영상 동시 전송은 422, 10MB 초과는 413.

### 사용 안내 (입력 전제)
- 사진은 **측면**(뒤에서 비구 방향으로) 어드레스 자세, 영상은 **정면** 스윙, **오른손잡이**만 판정합니다. 이 전제는 실행 중에 검사하지 않습니다.
- 사람이 없거나 필요한 관절이 잘 안 보이면(인식 신뢰도 0.5 미만) 판정하지 않고 다시 찍어 달라고 안내합니다.

## 5. 실행

```bash
pip install -r requirements.txt      # 상위 폴더 .venv 사용
./run.sh test                        # 단위 테스트 61개 (실제 사진 MediaPipe 테스트 포함)
./run.sh eval                        # test_queries.csv 자체 평가
./run.sh offline                     # 가짜 모델로 API 서버 (AWS 불필요)
./run.sh live                        # 실제 Bedrock·Chroma·MediaPipe (.env 필요)
GOLF_DEMO=1 ./run.sh live            # 라이브 + 더미 관절 좌표 데모 경로 열기
```

Windows PowerShell에서는 `python -m pytest -q`, `python -m src.evaluate --detail`, `$env:GOLF_MODE="offline"; python -m uvicorn src.app:app --port 8000` 처럼 직접 실행합니다.

**테스트 웹 페이지**: 서버를 띄우고 `http://localhost:8000` 을 엽니다. 상단 바, 왼쪽 프로필·교육용 기준값, 가운데 새 코칭 입력과 코칭 기록 피드, 오른쪽 추천 질문으로 구성됩니다.
- **진행 중 코칭**: 가운데 맨 위 카드가 지금 대화 중인 코칭(thread_id)입니다. 첫 질문을 보내면 그 카드가 그대로 코칭이 되고, 대화가 위에서 아래로 쌓입니다. 입력칸은 카드 아래에 붙어 스크롤해도 화면 하단에 남습니다. 같은 코칭에서는 서버도 앞의 대화를 이어서 답합니다.
- "+ 새 코칭"으로 새로 시작하면 앞 코칭은 아래 '지난 코칭'으로 내려가고, "이어서 코칭하기"로 다시 열 수 있습니다. 오른쪽 추천 질문을 누르면 새 코칭으로 입력칸에 들어갑니다.
- 기록은 브라우저(localStorage)에 저장되어 새로고침해도 남습니다. 다른 브라우저·기기와는 공유되지 않습니다. 카드의 "삭제", 왼쪽의 "기록 지우기"로 지웁니다.
- **사진 업로드**: 입력칸의 "사진·영상" 버튼으로 고르거나 입력칸에 끌어다 놓습니다. 사진은 브라우저에서 긴 변 1600px로 줄여 보냅니다(관절 좌표는 비율이라 판정에 영향 없음). 영상은 10MB 이하 원본을 보냅니다.
- 답변 말풍선에 상태, 문제 코드, 관절 각도 막대(초록 구간이 교육용 기준값), 근거 문서, 처리 단계, 응답 JSON이 함께 나옵니다. 오른쪽 추천 질문은 `test_queries.csv` 문항입니다.
- 오프라인 모드에서는 "판정용 관절 좌표"로 더미 fixture를 골라 판정을 시험합니다. 이 기능은 `/demo/fixture` 보조 경로를 씁니다. 라이브 모드에서는 `GOLF_DEMO=1`일 때만 열리고, 아니면 404입니다. `/query` 규약은 바뀌지 않습니다.
- 상단 "코칭 모델" 상자에서 Bedrock 모델 10개(Claude Sonnet 4.5·4.6, Haiku 4.5, Amazon Nova) 중 하나를 고릅니다. 고른 모델은 브라우저에 기억됩니다.
  - 모델은 코칭 답변 작성에만 쓰입니다. 검색(Titan 임베딩 + Chroma)과 자세 판정은 모델과 상관없이 같습니다.
  - 기본값은 고정 스택인 `us.anthropic.claude-sonnet-4-5-20250929-v1:0`입니다. 모델을 지정하지 않은 요청과 자동 채점은 기본값만 씁니다.
  - API로는 `POST /query`에 `"model": "<모델 ID>"`를 붙입니다. 목록은 `GET /models`이고, 목록에 없는 ID는 422입니다. 응답의 `model` 필드에 실제로 쓴 모델이 나옵니다.
  - 오프라인 모드에서는 어떤 모델을 골라도 가짜 모델(`FakeCoachLLM`)이 답합니다.
- Bedrock 사용량 한도에 걸리면 429와 "다른 코칭 모델을 골라 달라"는 안내가 나옵니다.

**사진 분석(MediaPipe)**: `mediapipe` 1.x의 PoseLandmarker(Tasks API)를 씁니다. Python 3.14에서 동작을 확인했습니다. 포즈 모델 파일(`data/models/pose_landmarker_full.task`, 약 9MB)은 처음 분석할 때 공식 주소에서 자동으로 내려받고 git에서는 뺐습니다.
- 측면 사진은 몸 반대편 다리가 가려지는 게 정상이라, 양쪽 어깨·엉덩이와 **잘 보이는 다리 하나 이상**(엉덩이-무릎-발목)이 있으면 판정합니다. 무릎 각도는 보이는 다리로 잽니다.
- 시험용 실제 사진 7장이 `data/test_photos/`에 있습니다(위키미디어 공용 CC 라이선스, 출처는 [SOURCES.md](data/test_photos/SOURCES.md)).
- **영상**: 정면에서 전신이 보이게 찍은 스윙 영상(10MB 이하)을 올리면, 영상 전체를 고르게 건너뛰며(최대 240프레임) 관절을 뽑고, 왼손목이 처음 충분히 올라갔다가 내려오는 지점을 백스윙 정점으로 잡아 왼팔 각도를 판정합니다. 판정은 모델이 아니라 코드가 하고, 모델은 결과를 설명만 합니다.
- 시험용 정면 스윙 영상 4개가 `data/test_videos/`에 있습니다(위키미디어 공용 CC0, 출처는 [SOURCES.md](data/test_videos/SOURCES.md)).

## 6. 평가

```
python -m src.evaluate --detail
```

- 오프라인(가짜 모델 + BM25 + 포즈 fixture)으로 20건을 돌리고, `expected_traits`·`forbidden` 중 기계로 확인할 수 있는 항목(status, issue_codes, sources, 고정 문구, 호출 경로)을 채점합니다.
- 톤·설명 품질처럼 기계로 확인할 수 없는 항목은 '수동 확인'으로 따로 출력합니다. 이 항목은 라이브 시연에서 사람이 확인합니다.
- 1차 개발 기준 결과는 [HISTORY.md](HISTORY.md)에 있습니다.

## 7. 한계

- 각도 기준값과 골프 문서는 모두 **교육용 더미 데이터**입니다. 실제 코칭 기준이 아닙니다.
- 사진 한 장으로는 스윙의 흐름(템포·순서)을 볼 수 없습니다. 사진은 어드레스 자세만 판정합니다.
- 2D 관절 좌표라 촬영 각도가 달라지면 측정값이 달라집니다.
- 골프 규칙은 2023년판 기준입니다. 다음 개정판(2028년 예정)이 나오면 문서를 갱신해야 합니다.
