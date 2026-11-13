"""POST /query: 표준 규약 필드, 알 수 없는 필드(pose_fixture) 거부, 입력 오류 코드."""

import base64

import pytest
from fastapi.testclient import TestClient

from src.api import app as app_module
from src.shared import config


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GOLF_MODE", "offline")
    monkeypatch.delenv("GOLF_DEMO", raising=False)
    app_module.get_agent.cache_clear()
    yield TestClient(app_module.app)
    app_module.get_agent.cache_clear()


def test_standard_contract(client):
    res = client.post("/query", json={"question": "퍼팅할 때 손목은 어떻게 써야 하나요?"})
    assert res.status_code == 200
    body = res.json()
    assert {"answer", "contexts", "trace"} <= body.keys()
    assert body["status"] == "answered" and "putting.md" in body["sources"]
    assert all({"step", "input", "output"} <= t.keys() for t in body["trace"])
    assert body["agents"] == [config.KNOWLEDGE_AGENT]  # supervisor 가 부른 sub-agent


def test_pose_fixture_field_is_rejected(client):
    res = client.post("/query", json={"question": "자세 봐주세요", "pose_fixture": {"kind": "photo", "landmarks": []}})
    assert res.status_code == 422


def test_invalid_base64_is_rejected(client):
    res = client.post("/query", json={"question": "자세 봐주세요", "image_base64": "@@not-base64@@"})
    assert res.status_code == 422


def test_oversized_media_is_rejected(client):
    big = base64.b64encode(b"0" * (config.MAX_MEDIA_BYTES + 1)).decode()
    res = client.post("/query", json={"question": "스윙 봐주세요", "video_base64": big})
    assert res.status_code == 413


def test_web_page_and_demo_samples(client):
    assert "SwingKid" in client.get("/").text
    data = client.get("/demo/samples").json()
    import csv

    from src.evaluation.evaluate import CSV_PATH

    n_queries = len(list(csv.DictReader(CSV_PATH.open(encoding="utf-8-sig"))))
    assert data["mode"] == "offline" and len(data["queries"]) == n_queries and "photo_spine_bad" in data["fixtures"]


def test_demo_fixture_works_only_offline(client, monkeypatch):
    body = {"question": "어드레스 봐주세요", "fixture": "photo_spine_bad"}
    res = client.post("/demo/fixture", json=body)
    assert res.status_code == 200 and res.json()["issue_codes"] == [config.SPINE_TILT]
    monkeypatch.setenv("GOLF_MODE", "live")
    assert client.post("/demo/fixture", json=body).status_code == 404


def test_models_list_and_validation(client):
    data = client.get("/models").json()
    assert data["default"] == config.MODEL_ID == data["models"][0]["id"] and len(data["models"]) == 10
    ok = client.post("/query", json={"question": "퍼팅할 때 손목은 어떻게 써야 하나요?", "model": data["models"][4]["id"]})
    assert ok.status_code == 200 and ok.json()["model"] == "FakeCoachLLM"
    bad = client.post("/query", json={"question": "퍼팅할 때 손목은 어떻게 써야 하나요?", "model": "gpt-4o"})
    assert bad.status_code == 422


def test_demo_flag_opens_fixture_route_in_live(monkeypatch):
    monkeypatch.setenv("GOLF_MODE", "live")
    monkeypatch.delenv("GOLF_DEMO", raising=False)
    assert app_module._demo_enabled() is False
    monkeypatch.setenv("GOLF_DEMO", "1")
    assert app_module._demo_enabled() is True


def test_photo_and_video_together_is_rejected(client):
    tiny = base64.b64encode(b"x").decode()
    res = client.post("/query", json={"question": "봐주세요", "image_base64": tiny, "video_base64": tiny})
    assert res.status_code == 422


def test_bedrock_throttling_becomes_429(client, monkeypatch):
    class ThrottlingError(Exception):
        response = {"Error": {"Code": "ThrottlingException", "Message": "Too many tokens per day"}}

    def boom(*args, **kwargs):
        raise ThrottlingError()

    monkeypatch.setattr(app_module.get_agent(), "ask", boom)
    res = client.post("/query", json={"question": "퍼팅할 때 손목은 어떻게 써야 하나요?"})
    assert res.status_code == 429 and "다른 코칭 모델" in res.json()["detail"]


def test_video_response_carries_angle_series(client):
    video = client.post("/demo/fixture", json={"question": "백스윙 봐주세요", "fixture": "video_bent_arm"}).json()
    s = video["angle_series"]
    assert s["metric"] == "lead_arm" and len(s["values"]) > 1 and s["values"][s["apex_index"]] is not None
    text = client.post("/query", json={"question": "퍼팅할 때 손목은 어떻게 써야 하나요?"}).json()
    assert text["angle_series"] is None


def test_concurrent_first_requests_share_one_agent(client):
    """화면을 처음 열 때 동시에 들어온 요청들도 같은 에이전트(같은 장기 기억)를 써야 합니다."""
    from concurrent.futures import ThreadPoolExecutor

    app_module.get_agent.cache_clear()
    with ThreadPoolExecutor(max_workers=6) as pool:
        agents = list(pool.map(lambda _: app_module.get_agent(), range(6)))
    assert len({id(a) for a in agents}) == 1


def test_media_samples_list_and_files(client):
    samples = client.get("/samples/media").json()["samples"]
    names = {s["name"] for s in samples}
    assert {"side_address_01.jpg", "too_small_people.jpg", "face_on_swing_1.webm"} <= names
    assert "SOURCES.md" not in names
    assert [s["kind"] for s in samples] == sorted((s["kind"] for s in samples), key=lambda k: k != "photo")  # 사진 먼저
    video = next(s for s in samples if s["name"] == "face_on_swing_1.webm")
    assert video["kind"] == "video" and video["label"] == "정면 스윙 영상 1"
    res = client.get(video["url"])
    assert res.status_code == 200 and res.headers["content-type"] == "video/webm" and len(res.content) == video["size"]
    assert client.get("/samples/media/SOURCES.md").status_code == 404
    assert client.get("/samples/media/..%2F..%2Fsrc%2Fapp.py").status_code == 404


def test_architecture_page_is_served_and_linked(client):
    res = client.get("/architecture")
    assert res.status_code == 200 and "SwingKidArch.mount" in res.text
    page = client.get("/").text
    assert 'id="architecture"' in page and 'id="archMount"' in page and "/static/arch.js" in page
    js = client.get("/static/arch.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]


def test_architecture_code_comes_from_real_source(client):
    """모듈을 누르면 실제 소스 파일에서 함수 단위로 자른 코드가 줄 번호와 함께 옵니다."""
    from src.api.arch_code import MODULES

    for module in MODULES:
        d = client.get(f"/architecture/code/{module}").json()
        assert d["snippets"] and all(s["code"].strip() for s in d["snippets"]), module
    coach = client.get("/architecture/code/coach").json()
    first = coach["snippets"][0]
    lines = (config.PROJECT_ROOT / first["file"]).read_text(encoding="utf-8").splitlines()
    assert first["code"].splitlines()[0] == lines[first["start"] - 1]          # 줄 번호가 실제 파일과 맞음
    assert coach["llm"] and any(".invoke(" in lines[n - 1] for n in first["highlight"])  # LLM 호출 줄 강조
    assert client.get("/architecture/code/..%2Fapp").status_code == 404
    assert client.get("/architecture/code/nope").status_code == 404
