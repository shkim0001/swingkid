"""실제 정면 스윙 영상으로 MediaPipe 영상 분석(정점 검출 + 왼팔 판정)을 확인합니다. 외부 API 호출 없음.

MediaPipe 나 포즈 모델 파일이 없는 환경(자동 채점 등)에서는 건너뜁니다.
영상 출처와 라이선스는 data/test_videos/SOURCES.md 에 있습니다.
"""

import pytest

from src import config
from src.tools import analyze_pose

pytest.importorskip("mediapipe")
pytest.importorskip("cv2")
if not config.POSE_MODEL_PATH.exists():
    pytest.skip("포즈 모델 파일이 없습니다(data/models/)", allow_module_level=True)

VIDEOS = config.PROJECT_ROOT / "data" / "test_videos"


@pytest.fixture(scope="module")
def findings():
    from src.tools import MediaPipeEstimator

    est = MediaPipeEstimator()
    return {i: analyze_pose(est.estimate_video((VIDEOS / f"face_on_swing_{i}.webm").read_bytes())) for i in range(1, 5)}


def test_every_clip_finds_the_backswing_top(findings):
    for i, f in findings.items():
        assert f.detected, f"{i}번: {f.reason}"
        assert f.apex_frame is not None and "lead_arm" in f.metrics


def test_straight_and_bent_lead_arms_are_told_apart(findings):
    # 1번은 기준(160°)과 가까워 판정 대신 검출만 확인합니다(SOURCES.md 참고).
    assert findings[3].issue_codes == []
    assert findings[2].issue_codes == [config.BENT_LEAD_ARM]
    assert findings[4].issue_codes == [config.BENT_LEAD_ARM]
