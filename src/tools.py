"""도메인 도구 — 포즈(관절 좌표)로 스윙 문제를 판정합니다.

핵심 원칙: 판정은 LLM이 아니라 이 파일의 '순수 함수'가 합니다.
같은 관절 좌표를 넣으면 항상 같은 문제 코드가 나오므로, 가짜 모델로도 결정적으로 채점할 수 있습니다.
LLM은 이 판정 결과를 받아 교정 설명 문장만 씁니다.

관절 좌표 형식 (MediaPipe Pose 33개 랜드마크와 같은 번호):
    {"x": 0~1, "y": 0~1, "visibility": 0~1}   # 이미지 좌표라 y는 아래로 갈수록 커집니다.

포즈 입력(pose) 형식:
    사진: {"kind": "photo", "landmarks": [33개]}
    영상: {"kind": "video", "frames": [[33개], ...], "apex_frame": 정점 프레임 번호 또는 None,
           "times": [프레임별 영상 시각(초), ...] (선택. 라이브 추정기가 채우며, 화면에서 영상과 그래프를 맞출 때 씀)}
자동 채점은 data/fixtures/*.json 에 이 형식으로 저장한 '교육용 더미 관절 좌표'를 주입합니다.
라이브 시연에서는 MediaPipeEstimator 가 실제 사진·영상에서 같은 형식을 만듭니다.

촬영 전제(사진=측면, 영상=정면, 오른손잡이)는 실행 중에 검사하지 않습니다. README에 사용 안내로 적습니다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from src import config

# MediaPipe Pose 랜드마크 번호
L_SHOULDER, R_SHOULDER = 11, 12
L_ELBOW, R_ELBOW = 13, 14
L_WRIST, R_WRIST = 15, 16
L_HIP, R_HIP = 23, 24
L_KNEE, R_KNEE = 25, 26
L_ANKLE, R_ANKLE = 27, 28

# 판정에 꼭 필요한 관절. visibility가 기준 미만이면 인식 실패로 봅니다.
# 사진: 양쪽 어깨·엉덩이는 모두 필요하고, 다리는 엉덩이-무릎-발목이 잘 보이는 쪽이 하나 이상이면 됩니다.
#       측면 사진에서는 몸 반대편 무릎·발목이 가려져 신뢰도가 낮게 나오는 것이 정상이기 때문입니다.
PHOTO_TORSO = [L_SHOULDER, R_SHOULDER, L_HIP, R_HIP]
LEGS = {"left": (L_HIP, L_KNEE, L_ANKLE), "right": (R_HIP, R_KNEE, R_ANKLE)}
VIDEO_REQUIRED = [L_SHOULDER, L_ELBOW, L_WRIST]


@dataclass
class PoseFinding:
    """포즈 판정 결과."""

    detected: bool
    kind: str                                   # "photo" | "video"
    issue_codes: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)   # 측정 각도(도)
    apex_frame: int | None = None
    reason: str = ""                            # 인식 실패 이유
    series: dict | None = None                  # 영상: 프레임별 왼팔 각도 (화면 그래프용, 판정에는 쓰지 않음)


# ── 기하 계산 ──────────────────────────────────────────────


def _pt(lm: dict) -> tuple[float, float]:
    return float(lm["x"]), float(lm["y"])


def _mid(a: dict, b: dict) -> tuple[float, float]:
    (ax, ay), (bx, by) = _pt(a), _pt(b)
    return (ax + bx) / 2, (ay + by) / 2


def joint_angle(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    """b를 꼭짓점으로 a-b-c가 이루는 각도(0~180도)."""
    v1 = (a[0] - b[0], a[1] - b[1])
    v2 = (c[0] - b[0], c[1] - b[1])
    n1, n2 = math.hypot(*v1), math.hypot(*v2)
    if n1 == 0 or n2 == 0:
        return 0.0
    cos = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
    return math.degrees(math.acos(cos))


def spine_tilt(landmarks: list[dict]) -> float:
    """척추 앞기울기: 엉덩이 가운데→어깨 가운데 선이 수직선에서 기운 각도."""
    hx, hy = _mid(landmarks[L_HIP], landmarks[R_HIP])
    sx, sy = _mid(landmarks[L_SHOULDER], landmarks[R_SHOULDER])
    dx, dy = sx - hx, sy - hy          # y가 아래로 커지므로 위쪽은 dy < 0
    return math.degrees(math.atan2(abs(dx), -dy))


def knee_flex(landmarks: list[dict], sides: list[str] | None = None) -> float:
    """무릎 각도: 고른 쪽(기본은 양쪽) 엉덩이-무릎-발목 각도의 평균."""
    angles = [joint_angle(*(_pt(landmarks[i]) for i in LEGS[side])) for side in (sides or list(LEGS))]
    return sum(angles) / len(angles)


def visible_legs(landmarks: list[dict]) -> list[str]:
    """엉덩이·무릎·발목 세 관절이 모두 기준 이상으로 보이는 다리("left"/"right")."""
    return [side for side, joints in LEGS.items() if _visible(landmarks, list(joints))]


def lead_arm_angle(landmarks: list[dict]) -> float:
    """왼팔(오른손잡이의 리드 팔) 어깨-팔꿈치-손목 각도."""
    return joint_angle(_pt(landmarks[L_SHOULDER]), _pt(landmarks[L_ELBOW]), _pt(landmarks[L_WRIST]))


def _visible(landmarks: list[dict] | None, required: list[int]) -> bool:
    if not landmarks or len(landmarks) < 33:
        return False
    return all(float(landmarks[i].get("visibility", 0.0)) >= config.MIN_VISIBILITY for i in required)


# ── 백스윙 정점 검출 (라이브 시연용) ────────────────────────


def _torso_length(frames: list[list[dict]]) -> float:
    """어깨 가운데~엉덩이 가운데 거리의 중앙값. 사람이 화면에 얼마나 크게 찍혔는지를 재는 잣대입니다."""
    lengths = []
    for f in frames:
        if f and len(f) > R_HIP:
            (sx, sy), (hx, hy) = _mid(f[L_SHOULDER], f[R_SHOULDER]), _mid(f[L_HIP], f[R_HIP])
            lengths.append(math.hypot(sx - hx, sy - hy))
    lengths.sort()
    return lengths[len(lengths) // 2] if lengths else 0.0


def detect_apex(frames: list[list[dict]], window: int = 3, neighborhood: int = 2) -> int | None:
    """백스윙 정점: 왼손목(15번)이 처음으로 충분히 올라갔다가 내려오기 시작하는 프레임을 찾습니다.

    - 영상 전체에서 가장 높은 프레임을 고르면 피니시(손이 더 높이 올라가는 경우가 많음)를 잡기 쉬워
      '처음 나타나는 최고점'을 찾습니다(이미지 좌표라 y가 작을수록 높음).
    - 한두 프레임 흔들림에 속지 않도록 이동평균으로 다듬고, 앞뒤 neighborhood 프레임 중 가장 높은 지점만 후보로 봅니다.
    - '충분히'의 기준은 화면 크기가 아니라 몸통 길이(어깨~엉덩이)입니다. 사람이 작게 찍혀도 같은 기준이 됩니다.
      어드레스보다 몸통 길이의 40% 이상 올라가야 하고, 그 뒤 더 올라가지 않은 채 20% 이상 내려와야(다운스윙) 정점으로 인정합니다.
    """
    ys = [float(f[L_WRIST]["y"]) for f in frames if f and len(f) > L_WRIST]
    n = len(ys)
    if n < 5:
        return None
    half = window // 2
    smooth = [sum(ys[max(0, i - half): i + half + 1]) / len(ys[max(0, i - half): i + half + 1]) for i in range(n)]
    head = sorted(smooth[:3])
    base = head[len(head) // 2]                          # 어드레스 손 높이
    torso = _torso_length(frames)
    unit = torso if torso >= 0.05 else 0.25              # 몸통을 못 재면 화면 비율 0.25로 대신함
    rise, drop, slack = 0.4 * unit, 0.2 * unit, 0.06 * unit

    def turns_down(i: int) -> bool:
        for y in smooth[i + 1:]:
            if y < smooth[i] - slack:                    # 더 올라감: 아직 정점이 아님
                return False
            if y - smooth[i] >= drop:                    # 충분히 내려옴: 다운스윙 시작
                return True
        return False

    for i in range(1, n - 1):
        if smooth[i] != min(smooth[max(0, i - neighborhood): i + neighborhood + 1]):
            continue
        if base - smooth[i] >= rise and turns_down(i):
            return i
    return None


# ── 판정 ────────────────────────────────────────────────────


def analyze_photo(landmarks: list[dict] | None) -> PoseFinding:
    """측면 어드레스 사진을 판정합니다. 척추 앞기울기와 무릎 각도를 교육용 기준값과 비교합니다.

    무릎 각도는 잘 보이는 다리만으로 잽니다(양쪽 다 보이면 평균).
    """
    legs = visible_legs(landmarks) if _visible(landmarks, PHOTO_TORSO) else []
    if not legs:
        return PoseFinding(False, "photo", reason="사람이 없거나 필요한 관절의 인식 신뢰도가 0.5 미만입니다")
    tilt, knee = spine_tilt(landmarks), knee_flex(landmarks, legs)
    codes = []
    lo, hi = config.SPINE_TILT_RANGE
    if not lo <= tilt <= hi:
        codes.append(config.SPINE_TILT)
    lo, hi = config.KNEE_FLEX_RANGE
    if not lo <= knee <= hi:
        codes.append(config.KNEE_FLEX)
    return PoseFinding(True, "photo", codes, {"spine_tilt": round(tilt, 1), "knee_flex": round(knee, 1)})


def lead_arm_series(frames: list[list[dict]], times: list[float] | None, apex: int) -> dict:
    """프레임별 왼팔 각도. 왼팔 관절이 안 보이는 프레임은 None 입니다.

    판정은 정점 한 프레임으로만 하고, 이 값들은 화면에서 영상과 함께 각도 변화를 보여 주는 데만 씁니다.
    times 가 없으면(채점용 fixture) t 는 None 이라 화면은 프레임 순서로만 그립니다.
    """
    values = [round(lead_arm_angle(f), 1) if _visible(f, VIDEO_REQUIRED) else None for f in frames]
    t = [round(float(x), 3) for x in times] if times and len(times) == len(frames) else None
    return {
        "metric": "lead_arm",
        "t": t,
        "values": values,
        "apex_index": apex,
        "threshold": config.LEAD_ARM_MIN,
    }


def analyze_video(
    frames: list[list[dict]] | None, apex_frame: int | None = None, times: list[float] | None = None
) -> PoseFinding:
    """정면 스윙 영상을 판정합니다. 백스윙 정점 프레임의 왼팔 각도를 교육용 기준값과 비교합니다.

    apex_frame 이 주어지면(자동 채점 fixture) 그 값을 쓰고, 없으면(라이브) detect_apex 로 찾습니다.
    """
    if not frames:
        return PoseFinding(False, "video", reason="영상에서 사람을 찾지 못했습니다")
    apex = apex_frame if apex_frame is not None else detect_apex(frames)
    if apex is None or not 0 <= apex < len(frames):
        return PoseFinding(False, "video", reason="백스윙 정점을 찾지 못했습니다")
    landmarks = frames[apex]
    if not _visible(landmarks, VIDEO_REQUIRED):
        return PoseFinding(False, "video", apex_frame=apex, reason="정점 프레임에서 왼팔 관절의 인식 신뢰도가 0.5 미만입니다")
    arm = lead_arm_angle(landmarks)
    codes = [config.BENT_LEAD_ARM] if arm < config.LEAD_ARM_MIN else []
    series = lead_arm_series(frames, times, apex)
    return PoseFinding(True, "video", codes, {"lead_arm": round(arm, 1)}, apex_frame=apex, series=series)


def analyze_pose(pose: dict | None) -> PoseFinding:
    """포즈 입력 종류(사진/영상)에 맞는 판정 함수를 부릅니다."""
    if not pose:
        return PoseFinding(False, "photo", reason="포즈 입력이 없습니다")
    if pose.get("kind") == "video":
        return analyze_video(pose.get("frames"), pose.get("apex_frame"), pose.get("times"))
    return analyze_photo(pose.get("landmarks"))


# ── 라이브 시연용 포즈 추정기 ───────────────────────────────


class MediaPipeEstimator:
    """실제 사진·영상에서 관절 좌표를 뽑습니다. (라이브 시연 전용)

    MediaPipe Tasks API(PoseLandmarker, mediapipe 1.x)를 씁니다. 예전 mp.solutions API 는 최신 버전에 없습니다.
    MediaPipe·OpenCV 는 여기서만 불러옵니다(지연 import). 그래서 이 패키지가 없는 채점 환경에서도
    나머지 코드와 테스트는 그대로 돌아갑니다.
    포즈 모델 파일(data/models/pose_landmarker_full.task)이 없으면 처음 쓸 때 공식 주소에서 한 번 내려받습니다.
    """

    def __init__(self, max_frames: int = 240, max_side: int = 1280, model_path=None):
        import mediapipe  # noqa: F401  (설치 여부를 먼저 확인)

        self.max_frames = max_frames
        self.max_side = max_side
        self.model_path = model_path or config.POSE_MODEL_PATH
        self._model: bytes | None = None

    def _model_bytes(self) -> bytes:
        if self._model is None:
            from pathlib import Path

            path = Path(self.model_path)
            if not path.exists():
                import urllib.request

                path.parent.mkdir(parents=True, exist_ok=True)
                urllib.request.urlretrieve(config.POSE_MODEL_URL, path)
            self._model = path.read_bytes()
        return self._model

    def _landmarker(self, video: bool):
        from mediapipe.tasks.python import BaseOptions, vision

        mode = vision.RunningMode.VIDEO if video else vision.RunningMode.IMAGE
        options = vision.PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_buffer=self._model_bytes()), running_mode=mode, num_poses=1
        )
        return vision.PoseLandmarker.create_from_options(options)

    def _to_mp_image(self, bgr):
        import cv2
        import mediapipe as mp

        h, w = bgr.shape[:2]
        scale = self.max_side / max(h, w)
        if scale < 1:  # 큰 사진은 줄여서 처리 속도를 맞춥니다(좌표는 0~1 비율이라 영향 없음)
            bgr = cv2.resize(bgr, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        return mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))

    @staticmethod
    def _landmarks(result) -> list[dict]:
        if not result.pose_landmarks:
            return []
        return [{"x": p.x, "y": p.y, "visibility": p.visibility} for p in result.pose_landmarks[0]]

    def estimate_image(self, data: bytes) -> dict:
        import cv2
        import numpy as np

        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return {"kind": "photo", "landmarks": []}
        with self._landmarker(video=False) as landmarker:
            result = landmarker.detect(self._to_mp_image(image))
        return {"kind": "photo", "landmarks": self._landmarks(result)}

    @staticmethod
    def gif_delay(ms) -> float:
        """GIF 프레임 표시 시간(초). 브라우저처럼 10ms 이하(또는 없음)는 100ms 로 봅니다(화면 재생 시각과 맞추려고)."""
        return 0.1 if not ms or ms <= 10 else ms / 1000

    def estimate_gif(self, data: bytes) -> dict:
        """움직이는 GIF 를 영상처럼 분석합니다. Pillow 로 프레임과 프레임별 표시 시간을 읽습니다."""
        import io

        import numpy as np
        from PIL import Image, ImageSequence

        frames: list[list[dict]] = []
        times: list[float] = []
        with Image.open(io.BytesIO(data)) as gif:
            total = getattr(gif, "n_frames", 1)
            stride = max(1, math.ceil(total / self.max_frames))
            clock = 0.0
            with self._landmarker(video=True) as landmarker:
                for index, frame in enumerate(ImageSequence.Iterator(gif)):
                    delay = self.gif_delay(frame.info.get("duration"))
                    if index % stride == 0 and len(frames) < self.max_frames:
                        bgr = np.ascontiguousarray(np.array(frame.convert("RGB"))[:, :, ::-1])
                        result = landmarker.detect_for_video(self._to_mp_image(bgr), int(clock * 1000))
                        landmarks = self._landmarks(result)
                        if landmarks:
                            frames.append(landmarks)
                            times.append(clock)
                    clock += delay
        return {"kind": "video", "frames": frames, "apex_frame": None, "times": times}

    def estimate_video(self, data: bytes) -> dict:
        if data[:4] == b"GIF8":   # 움직이는 GIF 도 스윙 영상으로 받습니다
            return self.estimate_gif(data)
        import os
        import tempfile

        import cv2

        # 파일 첫 바이트로 형식을 보고 확장자를 붙입니다(WebM/MKV 는 1A 45 DF A3, 그 밖은 mp4·mov 로 봄)
        suffix = ".webm" if data[:4] == b"\x1a\x45\xdf\xa3" else ".mp4"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            path = tmp.name
        frames: list[list[dict]] = []
        times: list[float] = []   # 각 프레임의 영상 시각(초). 화면에서 재생 위치와 각도 그래프를 맞춥니다
        try:
            cap = cv2.VideoCapture(path)
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            # 영상 전체를 고르게 봅니다. 프레임이 max_frames 보다 많으면 일정 간격으로 건너뜁니다
            # (앞부분만 자르면 어드레스가 긴 영상에서 스윙을 놓치기 때문).
            stride = max(1, math.ceil(total / self.max_frames)) if total > 0 else 1
            index = 0
            with self._landmarker(video=True) as landmarker:
                while len(frames) < self.max_frames:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    if index % stride == 0:
                        result = landmarker.detect_for_video(self._to_mp_image(frame), int(index * 1000 / fps))
                        landmarks = self._landmarks(result)
                        if landmarks:
                            frames.append(landmarks)
                            times.append(index / fps)
                    index += 1
            cap.release()
        finally:
            os.unlink(path)
        return {"kind": "video", "frames": frames, "apex_frame": None, "times": times}
