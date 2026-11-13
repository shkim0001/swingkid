"""실제 사진으로 MediaPipe 포즈 추정을 확인합니다 (외부 API 호출 없음, 로컬 추론만).

MediaPipe 나 포즈 모델 파일이 없는 환경(자동 채점 등)에서는 건너뜁니다.
사진 출처와 라이선스는 data/test_photos/SOURCES.md 에 있습니다.
"""

import pytest

from src.shared import config
from src.swing.pose import analyze_pose

pytest.importorskip("mediapipe")
pytest.importorskip("cv2")
if not config.POSE_MODEL_PATH.exists():
    pytest.skip("포즈 모델 파일이 없습니다(data/models/)", allow_module_level=True)

PHOTOS = config.PROJECT_ROOT / "data" / "test_photos"


@pytest.fixture(scope="module")
def estimator():
    from src.swing.estimator import MediaPipeEstimator

    return MediaPipeEstimator()


@pytest.mark.parametrize("name", [f"side_address_{i:02d}.jpg" for i in range(1, 7)])
def test_side_address_photos_are_judged(estimator, name):
    finding = analyze_pose(estimator.estimate_image((PHOTOS / name).read_bytes()))
    assert finding.detected, finding.reason
    assert {"spine_tilt", "knee_flex"} <= finding.metrics.keys()


def test_tiny_people_photo_is_not_detected(estimator):
    finding = analyze_pose(estimator.estimate_image((PHOTOS / "too_small_people.jpg").read_bytes()))
    assert not finding.detected


def test_broken_image_bytes_are_not_detected(estimator):
    assert not analyze_pose(estimator.estimate_image(b"not an image")).detected
