"""공통 LLM 클라이언트 — Bedrock 코칭 모델 생성, 모델 대체 순서, 응답 텍스트 추출.

여러 도메인(coach 등)이 같은 방식으로 모델을 만들도록 여기 한 곳에 둡니다.
실제 클라이언트(langchain_aws, botocore)는 함수 안에서만 불러와, 가짜 모델로 도는 채점 환경에 의존을 남기지 않습니다.
"""

from __future__ import annotations

from src.shared import config


def text_of(message) -> str:
    """ChatBedrockConverse 는 content 를 블록 목록으로 주기도 하므로 텍스트만 모읍니다."""
    content = getattr(message, "content", message)
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content)


def fallback_chain(model_id: str | None) -> list[str]:
    """코칭 모델을 시도할 순서: 고른 모델(없으면 기본 모델) → 허용 목록(config.AVAILABLE_MODELS)의 나머지 순서대로."""
    first = model_id or config.MODEL_ID
    return [first, *(m["id"] for m in config.AVAILABLE_MODELS if m["id"] != first)]


def default_llm(model_id: str = config.MODEL_ID, fallback: bool = True):
    """실제 코칭 모델. 기본값은 고정 스택이고, 웹 화면에서 고른 모델이면 그 ID로 만듭니다. 함수 안에서만 만듭니다.

    고른 모델이 사용량 한도(ThrottlingException)·권한·일시 장애 등 Bedrock 오류로 실패하면
    허용 목록의 다음 모델로 넘어갑니다(LangChain with_fallbacks). 실제로 답한 모델은 응답의
    response_metadata["model_name"] 에 남아 API 응답의 model 필드로 나갑니다.
    fallback=False 면 고른 모델만 씁니다(평가 --strict-model: 회차 간 같은 모델 조건을 지키기 위해).
    """
    from botocore.exceptions import BotoCoreError, ClientError
    from langchain_aws import ChatBedrockConverse

    llms = [ChatBedrockConverse(model=m, region_name=config.REGION, temperature=0) for m in fallback_chain(model_id)]
    if not fallback:
        return llms[0]
    return llms[0].with_fallbacks(llms[1:], exceptions_to_handle=(ClientError, BotoCoreError))
