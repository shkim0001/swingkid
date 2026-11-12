"""움직이는 GIF 를 스윙 영상으로 분석한다(라이브 포즈 추정기). MediaPipe·OpenCV·Pillow 가 없으면 건너뛴다."""

import pathlib

import pytest

from src import config

VIDEO = config.PROJECT_ROOT / "data" / "test_videos" / "face_on_swing_2.webm"


def test_gif_delay_follows_browser_rule():
    pytest.importorskip("mediapipe")
    from src.tools import MediaPipeEstimator

    assert MediaPipeEstimator.gif_delay(None) == 0.1      # 표시 시간 없음 → 100ms
    assert MediaPipeEstimator.gif_delay(10) == 0.1        # 10ms 이하 → 100ms (브라우저와 같음)
    assert MediaPipeEstimator.gif_delay(40) == 0.04


def test_animated_gif_is_analyzed_like_a_video(tmp_path: pathlib.Path):
    cv2 = pytest.importorskip("cv2")
    pytest.importorskip("mediapipe")
    Image = pytest.importorskip("PIL.Image")
    if not VIDEO.exists() or not config.POSE_MODEL_PATH.exists():
        pytest.skip("시험 영상이나 포즈 모델 파일이 없습니다")
    from src.tools import MediaPipeEstimator, analyze_pose

    # 시험 영상의 스윙 구간(3~12초)을 10fps GIF 로 만듭니다
    cap = cv2.VideoCapture(str(VIDEO))
    fps = cap.get(cv2.CAP_PROP_FPS)
    images, index = [], 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if 3.0 <= index / fps <= 12.0 and index % 3 == 0:
            images.append(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
        index += 1
    gif = tmp_path / "swing.gif"
    images[0].save(gif, save_all=True, append_images=images[1:], duration=100, loop=0)

    pose = MediaPipeEstimator().estimate_video(gif.read_bytes())   # GIF 서명(GIF8)을 보고 GIF 로 읽음
    assert pose["kind"] == "video" and len(pose["frames"]) == len(pose["times"]) > 50
    assert pose["times"][1] == pytest.approx(0.1) and pose["times"] == sorted(pose["times"])
    finding = analyze_pose(pose)
    assert finding.detected and finding.series["t"] is not None
    assert 3.5 <= pose["times"][finding.apex_frame] <= 5.5     # 원본 영상의 정점(7.3초)에서 3초를 뺀 근처
