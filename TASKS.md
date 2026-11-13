# TASKS · 훈련 관리 (SERVICE.md 6장)

한 번에 하나씩 진행합니다. 끝난 작업은 `[x]` 와 확인 결과를 적습니다.
2026-11-12: 범위를 '왼쪽 카드'에서 '별도 훈련 화면(달력·라이브러리·일정)'으로 넓혀 T4 부터 다시 나눴습니다.

## 끝난 작업 (작은 범위 1차)

- [x] **T1. 훈련 저장소와 규칙** — `src/training.py` (`tests/test_training.py` 22개, 전체 233 passed)
- [x] **T2. 훈련 API와 자동 추천 연결** — `src/app.py` (`tests/test_training_api.py` 7개, 전체 240 passed, 오프라인 평가 20/20)
- [x] **T3. 화면: 훈련 카드와 상세** (Playwright 데스크톱·390px) — T9 에서 '오늘의 훈련' 요약으로 줄임

## 확대 범위

- [x] **T4. 훈련 라이브러리 데이터** — `data/training_library.json`, `src/training.py` (2026-11-12, 25개·8개 분야, `tests/test_training_library.py` 30개 통과, 기존 추천 테스트 유지)
  - 문서 기반 25개 항목·8개 분야(하는 법·장소·소요 시간·준비물·난이도·1회 목표·근거 문서·관련 문제 코드)
  - 완료 조건: 검증 테스트(근거 문서 존재, key 중복 없음, 분야·장소·단위·난이도 값, 문제 코드 3개 모두 추천 항목 있음), 기존 추천 동작 유지

- [x] **T5. 일정 저장소와 달력 계산** — `src/training.py` (2026-11-12, `tests/test_training_schedule.py` 19개 통과)
  - 날짜 배치·요일 반복 루틴(기간), 월 달력 펼치기, 기록과 맞춰 완료·남음·지남 판정, 주·월 달성률
  - 완료 조건: 단위 테스트(루틴 요일·기간 경계, 같은 날 중복 계획, 완료 판정, 달성률 분모는 오늘까지)

- [x] **T6. 판정 기반 추천 플랜** (2026-11-12, `tests/test_training_proposals.py` 10개 통과. 판정 응답 연결은 T7)
  - 문제 코드 → 2주 플랜 제안(월·수·금), 받기·닫기
  - 완료 조건: 단위 테스트(제안 내용, 같은 제안 중복 없음, 받으면 루틴 생성)

- [x] **T7. 일정·라이브러리 API** (2026-11-12, `tests/test_training_api.py` 11개 통과, 판정 응답 `training_added` → `training_proposal`, 오프라인 평가 20/20)
  - 라이브러리 조회, 내 항목에 추가, 계획·루틴 추가·삭제, 월 달력, 제안 받기·닫기
  - 완료 조건: API 테스트(게스트 401, 남의 것 403, 판정 → 제안 생성)

- [x] **T8. 훈련 화면** — 상단 '코칭 | 훈련' 전환 (2026-11-12, Playwright 데스크톱·390px: 게스트 라이브러리·필터, 판정→제안→받기→루틴, 날짜 넣기·완료 체크·달성률, 라이브러리→일정 대화상자, 항목 상세 기록·메모, 모바일 아래쪽 시트, 가로 넘침·화면 오류 없음)
  - 월간 달력(완료·남음·지남 표시, 달 이동), 날짜 상세(계획·체크·기록·메모·추가), 오늘의 훈련, 라이브러리(분야 필터·상세·일정에 넣기), 루틴 만들기, 추천 플랜 받기
  - 완료 조건: Playwright 데스크톱·390px, 가로 넘침 없음, 화면 오류 없음

- [x] **T9. 코칭 화면 정리** (2026-11-12, T3 가운데 상세 제거·항목 상세는 대화상자로 통일, Playwright: 게스트·빈 상태·판정→답변 안내→훈련 화면·받기·오늘 체크·상세 대화상자) — 왼쪽 카드를 '오늘의 훈련' 요약으로, 답변의 추천 안내를 플랜 제안으로 연결

- [x] **T10. 각도 변화 그래프** — 판정과 연결된 항목 상세 (2026-11-12, `training.angle_trend`·`GET /training/drills/{id}/trend`, `tests/test_training_trend.py` 4개, Playwright 데스크톱·390px)

- [x] **T11. 코칭 반영** — 연습 요약을 코칭 근거로 (2026-11-12, `training.practice_summary`·에이전트 practice 경로, `tests/test_training_coaching.py` 26개, 전체 333 passed, 오프라인 평가 20/20)

- [x] **T12. 관리자 훈련 보기** — 사용자별 계획·기록·달성률, 지우기 (2026-11-12, `GET /admin/training`·`/admin/training/{username}`·`DELETE /admin/training/routines/{id}`, API 테스트 2개, Playwright 데스크톱·390px)

- [x] **T13. 문서 정리** — README·HISTORY·아키텍처, 전체 테스트·평가 결과 (2026-11-12, 334 passed, 오프라인 평가 20/20, 가드레일 스트레스 18/18)

## 계정 (SERVICE.md 계정 항목)

- [x] **T14. Google 계정 로그인** — `src/accounts/google.py` · `auth.py` · `api/app.py` · 로그인 화면 (2026-11-13, `tests/test_google_login.py` 28개, 전체 362 passed, 오프라인 평가 20/20, Playwright(Edge) 데스크톱·390px: 버튼 표시·취소 안내·Google 리디렉션 주소, 가로 넘침·화면 오류 없음. 결정: 아이디·비밀번호 가입은 함께 유지, 아이디는 이메일 앞부분·겹치면 숫자, 모든 Google 계정 허용)
  - `GET /auth/google/login` → Google 동의 화면(state·nonce 쿠키), `GET /auth/google/callback` → 코드 교환 · ID 토큰 검사(iss·aud·exp·nonce) · 처음이면 계정 생성 · 세션 쿠키
  - `GET /auth/providers` 로 화면이 Google 버튼 표시 여부를 앎. Worker 는 `X-Forwarded-Host` 를 넘기고, `.env` 의 `GOOGLE_REDIRECT_URI` 로 고정할 수도 있음
  - 새 라이브러리 없음(이미 있는 httpx 로 토큰 교환)
  - 완료 조건: 단위 테스트(아이디 만들기·겹침·admin 회피, 같은 sub 재로그인, 비밀번호 로그인 불가, ID 토큰 검사 실패 사례), API 테스트(가짜 Google 응답으로 콜백 → 로그인, state 불일치 거절, 미설정 시 숨김), 전체 테스트·오프라인 평가 유지
