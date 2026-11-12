"""채점용 포즈 fixture(교육용 더미 관절 좌표)를 만드는 스크립트.

실제 사람의 사진·영상이 아니라, 원하는 각도가 나오도록 기하 계산으로 만든 가짜 관절 좌표입니다.
그래서 초상권 문제가 없고, 같은 파일로 항상 같은 판정이 나옵니다.

실행: python data/fixtures/make_fixtures.py   (miniPJT 폴더에서)
"""

from __future__ import annotations

import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
NOTE = "교육용 더미 데이터입니다. 원하는 각도가 나오도록 계산으로 만든 가짜 관절 좌표입니다."


def _blank(visibility: float = 0.95) -> list[dict]:
    """33개 랜드마크를 화면 가운데 근처에 깔아 둡니다. 판정에 쓰는 관절은 뒤에서 덮어씁니다."""
    return [{"x": 0.5, "y": 0.5, "visibility": visibility} for _ in range(33)]


def _set(lms: list[dict], idx: int, p: tuple[float, float], vis: float = 0.95) -> None:
    lms[idx] = {"x": round(p[0], 4), "y": round(p[1], 4), "visibility": vis}


def _rotate(v: tuple[float, float], deg: float) -> tuple[float, float]:
    r = math.radians(deg)
    return (v[0] * math.cos(r) - v[1] * math.sin(r), v[0] * math.sin(r) + v[1] * math.cos(r))


def photo(tilt: float, knee: float) -> dict:
    """측면(뒤에서 비구 방향) 어드레스 자세. 오른쪽(+x)이 몸 앞쪽입니다."""
    lms = _blank()
    ankle = (0.48, 0.92)
    a = math.radians(12)                                   # 정강이가 앞으로 기운 정도
    knee_pt = (ankle[0] + 0.22 * math.sin(a), ankle[1] - 0.22 * math.cos(a))
    to_ankle = (ankle[0] - knee_pt[0], ankle[1] - knee_pt[1])
    n = math.hypot(*to_ankle)
    to_ankle = (to_ankle[0] / n, to_ankle[1] / n)
    # 무릎에서 발목 방향을 knee 도만큼 돌려 엉덩이 방향을 만듭니다. 위·뒤쪽(작은 y, 작은 x)을 고릅니다.
    cands = [_rotate(to_ankle, knee), _rotate(to_ankle, -knee)]
    to_hip = min(cands, key=lambda v: (v[1], v[0]))
    hip = (knee_pt[0] + 0.24 * to_hip[0], knee_pt[1] + 0.24 * to_hip[1])
    t = math.radians(tilt)
    shoulder = (hip[0] + 0.30 * math.sin(t), hip[1] - 0.30 * math.cos(t))
    for left, right, p in [(11, 12, shoulder), (23, 24, hip), (25, 26, knee_pt), (27, 28, ankle)]:
        _set(lms, left, p)
        _set(lms, right, (p[0] + 0.005, p[1]))
    _set(lms, 0, (shoulder[0] + 0.06, shoulder[1] - 0.03))  # 코
    return {"_note": NOTE, "kind": "photo", "view": "down-the-line", "landmarks": lms}


def video(arm: float, n_frames: int = 30, apex: int = 12) -> dict:
    """정면(face-on) 스윙. 정점 프레임의 왼팔 각도가 arm 도가 되도록 만듭니다.

    왼손목 높이(y)는 어드레스 → 정점(첫 최고점) → 임팩트 → 피니시(정점보다 더 높음) 순서로 움직입니다.
    피니시가 더 높게 만들어, 정점 검출이 '가장 높은 프레임'이 아니라 '첫 최고점'을 찾는지 확인할 수 있습니다.
    """
    shoulder = (0.55, 0.35)
    elbow = (shoulder[0] - 0.078, shoulder[1] - 0.104)          # 위·왼쪽으로 0.13 길이
    to_sh = ((shoulder[0] - elbow[0]) / 0.13, (shoulder[1] - elbow[1]) / 0.13)
    cands = [_rotate(to_sh, arm), _rotate(to_sh, -arm)]
    to_wr = min(cands, key=lambda v: v[1])                      # 손목이 더 위로 가는 쪽
    wrist_apex = (elbow[0] + 0.12 * to_wr[0], elbow[1] + 0.12 * to_wr[1])

    address_y, impact_y, finish_y = 0.70, 0.72, wrist_apex[1] - 0.08
    ys = []
    for i in range(n_frames):
        if i <= apex:
            ys.append(address_y + (wrist_apex[1] - address_y) * (i / apex))
        elif i <= 20:
            ys.append(wrist_apex[1] + (impact_y - wrist_apex[1]) * ((i - apex) / (20 - apex)))
        else:
            ys.append(impact_y + (finish_y - impact_y) * ((i - 20) / (n_frames - 1 - 20)))

    frames = []
    for i, y in enumerate(ys):
        lms = _blank()
        _set(lms, 11, shoulder)
        _set(lms, 12, (0.45, 0.35))
        _set(lms, 23, (0.53, 0.60))
        _set(lms, 24, (0.47, 0.60))
        if i == apex:
            _set(lms, 13, elbow)
            _set(lms, 15, wrist_apex)
        else:
            _set(lms, 13, (0.52, (shoulder[1] + y) / 2))
            _set(lms, 15, (0.50, y))
        frames.append(lms)
    return {"_note": NOTE, "kind": "video", "view": "face-on", "apex_frame": apex, "frames": frames}


def low_visibility_photo() -> dict:
    """관절은 있지만 무릎·발목 인식 신뢰도가 0.5 미만인 사진(인식 실패 확인용)."""
    data = photo(38, 155)
    for i in (25, 26, 27, 28):
        data["landmarks"][i]["visibility"] = 0.3
    return data


FIXTURES = {
    "photo_spine_bad": lambda: photo(tilt=52, knee=155),     # 척추 52° → spine_tilt_out_of_range
    "photo_knee_bad": lambda: photo(tilt=38, knee=171),      # 무릎 171° → knee_flex_out_of_range
    "photo_normal": lambda: photo(tilt=38, knee=155),        # 둘 다 기준 안 → 문제 없음
    "photo_no_person": lambda: {"_note": NOTE, "kind": "photo", "landmarks": []},
    "photo_low_visibility": low_visibility_photo,
    "video_bent_arm": lambda: video(arm=138),                # 왼팔 138° → bent_lead_arm
    "video_normal": lambda: video(arm=172),                  # 왼팔 172° → 문제 없음
}


if __name__ == "__main__":
    for name, build in FIXTURES.items():
        path = HERE / f"{name}.json"
        path.write_text(json.dumps(build(), ensure_ascii=False), encoding="utf-8")
        print("wrote", path.name)
