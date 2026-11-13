# 작업 규칙

- 무엇을 만드는지는 SERVICE.md가 기준이다. 애매하면 추측하지 말고 물어본다.

- 스택: Python, LangGraph, Bedrock (anthropic.claude-3-5-sonnet-20241022-v2:0) -> 필요 시 모델은 조정해서 사용하거나 복수의 모델을 상황에 맞춰 변경하여 중단없이 쓸 수 있게 한다.

- 괜찮은 레퍼런스 코드를 깃허브에서 찾아서 레퍼런스로 삼거나 (있을 경우) 현재 ref 폴더 내 코드를 참조한다. 절대 사용자의 허락없이 신규 프레임워크 또는 라이브러리를 들이지 않는다.

- 한 번에 TASKS.md의 작업 하나만 한다.

- 계산, 규칙 판정은 LLM이 아니라 파이썬 코드로 하는 원칙으로 진행한다. 필요시 LLM 판정부분을 덧붙여 추가한다.

- API 키는 .env에서 읽는다.

# 소스 구조 (`src/` 도메인별 폴더)

모든 파이썬 소스는 `src/` 아래에 둔다. 폴더는 기술 역할(utils, models)이 아니라 **에이전트의 도메인(기능)** 으로 나눈다.

| 폴더 | 도메인 | 들어 있는 것 |
|---|---|---|
| `src/coach/` | 메인 에이전트 | `graph.py` LangGraph 그래프(start → guard → supervisor → sub-agent → coach)·`build_agent`, `prompts.py` 코칭 시스템 프롬프트·프롬프트 조립, `schemas.py` `AgentState`·`/query`·`/demo/fixture` 요청·응답 |
| `src/guard/` | 가드레일 | `rules.py` 정규식 차단 규칙(`check`, `about_pose`, `is_follow_up` …), `schemas.py` `GuardResult` |
| `src/swing/` | 자세 판정 (swing_agent) | `agent.py` SwingAgent, `pose.py` 각도 계산·문제 코드 판정 순수 함수, `estimator.py` MediaPipe 추정기, `schemas.py` `PoseFinding` |
| `src/knowledge/` | 골프 지식 RAG (knowledge_agent) | `agent.py` KnowledgeAgent, `retriever.py` BM25·Chroma·하이브리드 검색기, `load_doc_text`, `schemas.py` `Chunk` |
| `src/web_info/` | 웹 정보 (info_agent) | `agent.py` InfoAgent, `search.py` MCP·DuckDuckGo·가짜 검색기, `mcp_server.py` stdio MCP 서버, `schemas.py` `WebResult` |
| `src/memory/` | 장기 기억 | `store.py` 사용자별 판정 기록(LangGraph Store) |
| `src/history/` | 코칭 내역 | `store.py` 계정별 대화 목록·대화방 소유권 |
| `src/training/` | 훈련 관리 | `service.py` 라이브러리·항목·기록·일정·플랜 제안·연습 요약, `schemas.py` 훈련 API 요청 |
| `src/accounts/` | 계정 | `auth.py` 가입·로그인·세션·Google 계정 연결, `google.py` Google OAuth·ID 토큰 검사, `avatar.py` 캐릭터 아이콘, `schemas.py` 인증 요청·`GoogleIdentity` |
| `src/evaluation/` | 자체 평가 | `evaluate.py` 코드 채점 러너, `calibrate.py` 검색 보정, `judge.py` LLM 심사, `prompts.py` 심사 루브릭·프롬프트, `schemas.py` `JudgeResult`·`JudgeCase`·`Verdict` |
| `src/api/` | HTTP 진입점 | `app.py` FastAPI 라우트(도메인 함수를 조립만 함), `arch_code.py` 아키텍처 코드 보기, `static/` 웹 화면 |
| `src/shared/` | 공통 | `config.py` 설정값·고정 문구·기준값, `llm.py` Bedrock LLM 클라이언트·모델 대체 순서, `fakes.py` 가짜 모델, `trace.py` trace 단계 유틸 |

## 파일 배치 규칙

- 도메인 폴더 안의 파일 이름은 역할로 맞춘다.
  - `agent.py`: supervisor 가 부르는 sub-agent (`run(state) -> dict`). LLM 을 부르지 않는다.
  - `prompts.py`: 그 도메인이 LLM 에 보내는 프롬프트 문장과 조립 함수. 프롬프트 문장은 여기에만 둔다.
  - `schemas.py`: 입출력 타입(dataclass, TypedDict, pydantic 요청·응답 모델). 로직을 넣지 않는다.
  - 그 밖의 실행 로직은 하는 일을 드러내는 이름(`rules.py`, `pose.py`, `retriever.py`, `store.py`, `service.py` …).
- 새 기능은 먼저 어느 도메인인지 정하고 그 폴더에 넣는다. 맞는 도메인이 없으면 새 도메인 폴더(`__init__.py` 에 한 줄 설명)를 만든다.
- `src/shared/` 에는 **여러 도메인이 함께 쓰는** LLM 클라이언트·설정·유틸만 둔다. 한 도메인만 쓰는 코드는 그 도메인에 둔다.
- 도메인 사이 import 는 `from src.<도메인>.<모듈> import 이름` 절대 경로로 한다. `shared` 는 다른 도메인을 import 하지 않는다.
- 다른 도메인의 `_` 로 시작하는 이름은 가져다 쓰지 않는다. 필요하면 공개 이름으로 바꾼다.
- `api/app.py` 는 요청 검사와 도메인 함수 호출만 한다. 판정·집계 로직과 요청 모델은 각 도메인에 둔다.
- 테스트는 `tests/` 에 두고, 실행 명령은 `python -m pytest -q`, `python -m src.evaluation.evaluate`, `python -m uvicorn src.api.app:app` 이다.