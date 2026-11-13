#!/usr/bin/env bash
# 골프 입문 자세 코치 실행 스크립트 (miniPJT 폴더에서 실행)
#
#   ./run.sh test      단위 테스트 (실제 API 호출 없음)
#   ./run.sh eval      test_queries.csv 오프라인 자체 평가 (케이스별 근거 출력)
#   ./run.sh offline   API 서버 실행 — 가짜 모델 + BM25 (AWS 자격 증명 없이 시연)
#   ./run.sh live      API 서버 실행 — Bedrock + Titan/Chroma + MediaPipe (.env 의 AWS 자격 증명 필요)
#   ./run.sh fixtures  채점용 포즈 fixture(더미 관절 좌표) 다시 만들기
set -euo pipefail
cd "$(dirname "$0")"

case "${1:-test}" in
  test)     python -m pytest -q ;;
  eval)     python -m src.evaluation.evaluate --detail ;;
  offline)  GOLF_MODE=offline python -m uvicorn src.api.app:app --port "${PORT:-8000}" ;;
  live)     GOLF_MODE=live python -m uvicorn src.api.app:app --port "${PORT:-8000}" ;;
  fixtures) python data/fixtures/make_fixtures.py ;;
  *) echo "사용법: ./run.sh [test|eval|offline|live|fixtures]"; exit 1 ;;
esac
