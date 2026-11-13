"""가드레일 — 검색·포즈 분석·LLM 호출보다 먼저 실행하는 규칙 기반 차단.

규칙 기반(정규식)이라 같은 질문에는 항상 같은 결과가 나옵니다. 그래서 가짜 모델로도 채점할 수 있습니다.

차단 코드는 정확히 하나만 돌려줍니다. 여러 규칙에 걸리면 아래 우선순위로 고릅니다.
    guard_medical > guard_purchase > guard_off_topic
예) "허리가 아픈데 어드레스 자세 때문일까?" → 기술 질문이 섞여 있어도 의학(guard_medical)으로 차단합니다.

주의: 반대로 정상 질문을 막지 않는 것(오탐 금지)도 중요합니다.
"라운드 전 스트레칭", "상금 순위"처럼 차단어와 비슷해 보이는 정상 질문은 막지 않도록 패턴을 좁게 둡니다.
"""

from __future__ import annotations

import re

from src.guard.schemas import GuardResult
from src.shared import config


# 부상·통증 상담. '스트레칭'은 정상 준비 운동이라 여기에 넣지 않습니다.
_MEDICAL = re.compile(r"아프|아픈|아파|통증|부상|다쳤|다친|병원|치료|재활|염증|디스크|저려|저림|파스"
    # 통증을 돌려 말하는 표현 (2026-11-12 점검에서 '팔꿈치가 시큰거려요'가 답변으로 샘)
    r"|시큰|쑤시|쑤셔|결려|결림|붓기|부었|부어\s*올")

# 장비 구매·브랜드 추천, 레슨비·가격.
# '상금'은 가격 질문이 아니므로 넣지 않고, '추천'은 장비 단어와 함께 나올 때만 봅니다.
_PURCHASE = re.compile(
    r"브랜드|가격|얼마예요|얼마에요|얼마야|레슨비|그린피|구매|구입|살까|사도 될|사야|중고"
    r"|(드라이버|아이언|퍼터|웨지|클럽|장비|용품|골프공|공)\S*\s*(추천|사도|살까|사야|사려|사고\s*싶|사면|사요|사는\s*게)"
    r"|(뭐|뭘|무엇을|어떤\s*\S+)\s*(사요|사야|살까|사면|사는\s*게)"
    # 브랜드 이름·비교. '핑'은 다른 말과 겹쳐서 넣지 않습니다.
    r"|타이틀리스트|캘러웨이|테일러메이드|젝시오|미즈노|브리지스톤|스릭슨|혼마|코브라|PXG",
    re.I,
)

# 개인정보 요청과 프롬프트 인젝션. 전용 코드가 없어 guard_off_topic으로 묶습니다.
_PRIVACY = re.compile(r"연락처|전화번호|휴대폰|핸드폰|번호\s*좀|주소|이메일|명단|주민")
_INJECTION = re.compile(
    r"(이전|위의|위|앞의|앞|기존|모든)\s*(지시|명령|규칙).{0,6}(무시|잊)|시스템\s*프롬프트|system\s*prompt|너의\s*(규칙|설정|지시)"
    # 역할 바꾸기·권한 주장·지시문 노출 요청 (2026-11-12 점검에서 새던 표현)
    r"|개발자\s*모드|developer\s*mode|관리자\s*(권한|모드)|탈옥|jailbreak|가드레일\s*(해제|무시|꺼|끄)"
    r"|제한\s*없이\s*답|이제부터\s*(너는|넌)|지시문|^\s*SYSTEM\s*:"
    r"|ignore\s+(all\s+)?(previous|prior|above)|(previous|prior)\s+instructions",
    re.I,
)

# 골프와 무관한 질문을 가르기 위한 골프 단어 목록입니다.
# 이 단어가 하나도 없고 사진·영상도 없으면 골프와 무관한 질문으로 봅니다.
_GOLF_WORDS = re.compile(
    r"골프|스윙|그립|어드레스|드라이버|아이언|웨지|퍼터|퍼팅|숏게임|어프로치|벙커|그린|티샷|티|백스윙|다운스윙|임팩트|피니시"
    r"|라운드|홀|벌타|OB|오비|해저드|페널티|잠정구|분실구|에티켓|슬라이스|훅|뒤땅|토핑|비거리|연습장|레슨|자세|교정|타수|핸디캡"
    r"|투어|LPGA|PGA|KLPGA|오픈|캐디|프로|선수|클럽|공"
    # 자세 코칭에서 쓰는 신체·자세 단어 (통증·부상 표현은 위의 _MEDICAL 이 먼저 잡습니다)
    r"|척추|무릎|어깨|팔꿈치|손목|코킹|릴리스|캐스팅|골반|엉덩이|상체|하체|체중|발목|왼팔|오른팔|시선|머리\s*위치"
    # 지식 문서(구질·장비·규칙·스코어·용어·코스·연습·역사)에서 쓰는 용어.
    # '보기'(영화 보기)·'라이'(라이브)·'경사'(오르막길)·'루틴'(아침 루틴)처럼 일상어와 겹치는 말은 골프 표현으로만 넣습니다.
    r"|드로우|페이드|푸시\s*샷|풀\s*샷|생크|스카이볼|구질|미스샷|팔로스루|다운블로|하프\s*스윙|빈\s*스윙|스윙\s*템포"
    r"|러프|페어웨이|우드|유틸리티|하이브리드|로프트|샤프트|딤플|골프공|골프화|볼마커|웨지|샌드\s*웨지|벙커샷"
    r"|드롭|구제|언플레이어블|깃대|오구|로컬\s*룰|수리지"
    r"|버디|더블\s*보기|트리플\s*보기|보기\s*플레이|이글|알바트로스|홀인원|파\s*[345]|양파|더블\s*파|스코어"
    r"|매치\s*플레이|스트로크\s*플레이|스테이블포드|포섬|포볼|스크램블|멀리건|컨시드|도그레그|디봇|칩샷|피치샷|스위트\s*스폿"
    r"|티잉|티박스|티\s*마커|그늘집|머리\s*올|필드\s*(에|에서|라운드|레슨|감각|나가)|스크린\s*골프|프리샷|레이업|맞바람|뒷바람|옆바람|코스\s*공략"
    r"|(발끝|왼발)\s*(오르막|내리막)|경사지|라이가|메이저|마스터스|디\s*오픈|R&A|USGA|세인트앤드루스",
    re.I,
)

# 골프가 아닌 다른 종목. '프로야구'처럼 골프 단어(프로·선수·공·오픈 등)가 섞여도
# 다른 종목 단어가 있고 '골프'라는 말이 없으면 골프와 무관한 질문으로 봅니다.
_OTHER_SPORTS = re.compile(
    r"야구|축구|농구|배구|하키|테니스|탁구|배드민턴|볼링|당구|수영|마라톤|e스포츠|KBO|MLB|NBA|EPL|K리그", re.I
)


def check(question: str, has_media: bool = False, in_conversation: bool = False) -> GuardResult:
    """질문을 검사해 차단 여부와 guard_ 코드 하나를 돌려줍니다.

    in_conversation: 같은 대화(thread_id)에서 이미 코칭 답변을 주고받았는지.
    그렇다면 골프 단어가 없는 질문("그럼 하루에 몇 번 해요?")도 앞 대화를 잇는 질문으로 보고
    '골프와 무관' 판정만 건너뜁니다.

    골프와 무관한 질문은 차단(blocked)이 아니라 golf=False 로 돌려줍니다. 에이전트는 이때
    문서 검색·웹 검색·LLM 을 부르지 않고 no_answer 로 끝냅니다. 골프 관련 질문만 검색합니다. 의학·구매·개인정보·프롬프트 인젝션 차단은 그대로이고,
    근거 문서가 없으면 검색 단계에서 no_answer 로 끝나므로 지어낸 답이 나가지 않습니다.
    """
    q = question.strip()
    if _MEDICAL.search(q):
        return GuardResult(True, config.GUARD_MEDICAL, "부상·통증 등 의학 상담 요청")
    if _PURCHASE.search(q):
        return GuardResult(True, config.GUARD_PURCHASE, "장비 구매·브랜드 추천 또는 가격 문의")
    if _INJECTION.search(q):
        return GuardResult(True, config.GUARD_OFF_TOPIC, "지시 무시·시스템 프롬프트 요청(프롬프트 인젝션)")
    if _PRIVACY.search(q):
        return GuardResult(True, config.GUARD_OFF_TOPIC, "개인정보 요청")
    if not has_media and _OTHER_SPORTS.search(q) and "골프" not in q:
        return GuardResult(False, None, "다른 종목 질문(골프와 무관)", golf=False)
    if not has_media and not in_conversation and not _GOLF_WORDS.search(q) and not is_follow_up(q):
        return GuardResult(False, None, "골프와 무관한 질문", golf=False)
    return GuardResult(False)


def blocked_answer(code: str) -> str:
    """차단 응답 문구. 의학 차단에는 전문가 상담 안내를 덧붙입니다."""
    if code == config.GUARD_MEDICAL:
        return f"{config.BLOCKED_TEXT} {config.MEDICAL_SUFFIX}"
    return config.BLOCKED_TEXT


def mentions_golf(question: str) -> bool:
    """골프·자세 단어가 들어 있는지 (이어지는 질문에 직전 판정 근거를 붙일지 정할 때 씁니다)."""
    return bool(_GOLF_WORDS.search(question))


_FOLLOW_UP = re.compile(r"아까|방금|앞에서|그거|그 방법|그 교정|말한|알려준")


# 직전 사진·영상 판정에 이어 '내 자세의 이 부분은 어때?'를 묻는 표현 (신체 부위·스윙 동작·판정 이야기)
_POSE_FOLLOW_UP = re.compile(
    r"손목|코킹|스냅|릴리스|캐스팅|팔꿈치|왼팔|오른팔|팔|어깨|척추|무릎|허리|골반|엉덩이|상체|하체|체중|발목|머리|시선"
    r"|자세|스윙|백스윙|다운스윙|정점|임팩트|피니시|각도|판정|교정|고칠|괜찮|어때|봐\s*(줘|주세요|줄래)"
)


def about_pose(question: str) -> bool:
    """직전 사진·영상 판정이 있는 대화에서, 그 판정(내 자세)에 대한 이어지는 질문인지 봅니다.

    '손목 스냅도 봐줘', '코킹은 괜찮아요?', '손목은 어떻게 써야 해요?'처럼 신체 부위·스윙 동작을 묻거나
    '봐 줘·어때·괜찮아' 같은 평가 요청이면 판정에 이어지는 질문으로 봅니다.
    """
    return bool(_POSE_FOLLOW_UP.search(question))


def is_follow_up(question: str) -> bool:
    """'아까 알려준 교정 방법은…'처럼 이전 대화를 잇는 질문인지 봅니다.

    이런 질문은 골프 단어가 없어도 같은 대화(thread_id) 안에서는 정상 질문입니다.
    """
    return bool(_FOLLOW_UP.search(question))
