"""포즈 판정: 더미 관절 좌표 fixture 로 문제 코드·정상·인식 실패·정점 검출을 확인한다."""

import pytest

from src.shared import config
from src.swing.pose import analyze_pose, detect_apex
from tests.conftest import load_fixture


@pytest.mark.parametrize(
    "name, codes",
    [
        ("photo_spine_bad", [config.SPINE_TILT]),
        ("photo_knee_bad", [config.KNEE_FLEX]),
        ("photo_normal", []),
        ("video_bent_arm", [config.BENT_LEAD_ARM]),
        ("video_normal", []),
    ],
)
def test_issue_codes(name, codes):
    finding = analyze_pose(load_fixture(name))
    assert finding.detected
    assert finding.issue_codes == codes


@pytest.mark.parametrize("name", ["photo_no_person", "photo_low_visibility"])
def test_pose_not_detected(name):
    assert not analyze_pose(load_fixture(name)).detected


@pytest.mark.parametrize("name", ["video_bent_arm", "video_normal"])
def test_apex_detection_matches_stored_frame(name):
    """라이브 시연용 정점 검출이 fixture 에 저장된 정점과 ±2프레임 이내인지 (피니시를 잡으면 실패)."""
    data = load_fixture(name)
    assert abs(detect_apex(data["frames"]) - data["apex_frame"]) <= 2


def test_video_without_apex_uses_detection():
    data = load_fixture("video_bent_arm")
    data["apex_frame"] = None
    finding = analyze_pose(data)
    assert finding.detected and finding.issue_codes == [config.BENT_LEAD_ARM]


def test_side_photo_with_far_leg_hidden_is_still_judged():
    """측면 사진은 몸 반대편 다리가 가려지는 게 정상이라, 보이는 다리 하나로 무릎 각도를 잽니다."""
    from src.swing.pose import L_ANKLE, L_KNEE, analyze_photo, knee_flex

    landmarks = load_fixture("photo_knee_bad")["landmarks"]
    for i in (L_KNEE, L_ANKLE):
        landmarks[i] = {**landmarks[i], "visibility": 0.2}
    finding = analyze_photo(landmarks)
    assert finding.detected
    assert finding.metrics["knee_flex"] == round(knee_flex(landmarks, ["right"]), 1)


def test_photo_without_any_visible_leg_is_not_detected():
    from src.swing.pose import analyze_photo

    finding = analyze_photo(load_fixture("photo_low_visibility")["landmarks"])
    assert not finding.detected


def _shrink(frames, scale, cx=0.5, cy=0.5):
    """사람을 화면 가운데 쪽으로 작게 만든 영상 프레임."""
    return [[{**p, "x": cx + (p["x"] - cx) * scale, "y": cy + (p["y"] - cy) * scale} for p in f] for f in frames]


def test_apex_detection_works_for_a_small_person():
    """정점 기준이 화면 비율이 아니라 몸통 길이라, 사람이 작게 찍혀도 정점을 찾습니다."""
    data = load_fixture("video_normal")
    small = _shrink(data["frames"], 0.35)
    assert abs(detect_apex(small) - data["apex_frame"]) <= 2


def test_apex_detection_ignores_jitter_on_the_way_up():
    """올라가는 도중의 작은 흔들림(국소 최고점)을 정점으로 잡지 않습니다."""
    data = load_fixture("video_bent_arm")
    frames = data["frames"]
    for i in range(3, data["apex_frame"] - 2, 2):     # 백스윙 중 2프레임마다 손목을 살짝 내림
        frames[i][15] = {**frames[i][15], "y": frames[i][15]["y"] + 0.02}
    assert abs(detect_apex(frames) - data["apex_frame"]) <= 2


def test_video_finding_has_per_frame_angle_series():
    """영상 판정은 화면 그래프용 프레임별 왼팔 각도를 함께 돌려줍니다(판정 값은 정점 프레임 그대로)."""
    from src.swing.pose import analyze_pose

    pose = load_fixture("video_bent_arm")
    finding = analyze_pose(pose)
    s = finding.series
    assert len(s["values"]) == len(pose["frames"]) and s["t"] is None   # fixture 에는 영상 시각이 없음
    assert s["apex_index"] == pose["apex_frame"] and s["values"][s["apex_index"]] == finding.metrics["lead_arm"]
    assert s["threshold"] == config.LEAD_ARM_MIN

    timed = dict(pose, times=[i / 10 for i in range(len(pose["frames"]))])
    assert analyze_pose(timed).series["t"][:3] == [0.0, 0.1, 0.2]


def test_photo_has_no_angle_series():
    from src.swing.pose import analyze_pose

    assert analyze_pose(load_fixture("photo_spine_bad")).series is None
