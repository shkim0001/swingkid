"""swing 도메인 포즈 추정기 — 실제 사진·영상·GIF 에서 MediaPipe 로 관절 좌표를 뽑습니다(라이브 전용).

출력 형식은 pose.py 의 포즈 입력(pose) 형식과 같습니다.
"""

from __future__ import annotations

import math

from src.shared import config


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
