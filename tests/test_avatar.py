"""캐릭터 아이콘: 가입할 때 무작위로 정해지고, 고정 부품만으로 그려지며, 본인만 받을 수 있는지 확인한다."""

import random
import sqlite3

import pytest
from fastapi.testclient import TestClient

from src import app as app_module
from src.auth import AccountStore
from src.avatar import PARTS, is_valid, new_avatar, render_svg


def test_new_avatar_uses_known_parts_and_varies():
    rng = random.Random(1)
    avatars = [new_avatar(rng) for _ in range(30)]
    assert all(is_valid(a) for a in avatars)
    assert len({tuple(sorted(a.items())) for a in avatars}) >= 25   # 30명 중 거의 다 다름


@pytest.mark.parametrize("top", PARTS["top"])
def test_every_part_renders(top):
    for eyes in PARTS["eyes"]:
        for mouth in PARTS["mouth"]:
            a = {"body": PARTS["body"][0], "bg": PARTS["bg"][0], "top": top, "eyes": eyes, "mouth": mouth,
                 "pattern": PARTS["pattern"][1], "cheeks": True}
            svg = render_svg(a)
            assert svg.startswith("<svg") and svg.endswith("</svg>") and "<script" not in svg


def test_render_is_deterministic_and_rejects_unknown_parts():
    a = new_avatar(random.Random(3))
    assert render_svg(a) == render_svg(dict(a))
    with pytest.raises(ValueError):
        render_svg({**a, "body": '"/><script>alert(1)</script>'})


def test_signup_stores_avatar_and_old_accounts_get_one(tmp_path):
    db = tmp_path / "accounts.sqlite"
    conn = sqlite3.connect(db)   # 아이콘 칸이 없던 예전 DB
    conn.execute("CREATE TABLE users (username TEXT PRIMARY KEY, pw_salt BLOB NOT NULL, pw_hash BLOB NOT NULL, created_at TEXT NOT NULL)")
    conn.execute("INSERT INTO users VALUES ('old', x'00', x'00', '2026-11-12')")
    conn.commit(); conn.close()
    store = AccountStore(str(db))
    first = store.avatar("old")
    assert is_valid(first) and store.avatar("old") == first   # 한 번 정하면 그대로
    store.signup("alice", "swing-1234")
    assert is_valid(store.avatar("alice"))
    assert store.avatar("nobody") is None


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GOLF_MODE", "offline")
    app_module.get_agent.cache_clear()
    app_module.get_accounts.cache_clear()
    yield TestClient(app_module.app)
    app_module.get_agent.cache_clear()
    app_module.get_accounts.cache_clear()


def test_avatar_api(client):
    assert client.get("/auth/avatar.svg").status_code == 401
    client.post("/auth/signup", json={"username": "alice", "password": "swing-1234"})
    r = client.get("/auth/avatar.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert r.text == client.get("/auth/avatar.svg").text   # 매번 같은 캐릭터
