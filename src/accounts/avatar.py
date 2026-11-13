"""사용자 캐릭터 아이콘 — 가입할 때 부품을 무작위로 골라 저장하고, 그 조합으로 SVG 를 그립니다.

- 부품: 몸 색 · 배경색 · 머리 장식(귀·모자·새싹 등) · 눈 · 입 · 볼터치 · 몸 무늬.
  조합이 수만 가지라 2~10명 사이에서는 서로 잘 구분됩니다.
- 고르는 것만 무작위이고, 그리는 것은 같은 조합이면 항상 같은 그림이 나오는 순수 함수입니다.
- SVG 에는 아래 고정 목록의 값만 들어갑니다(사용자 입력이 들어가지 않으므로 스크립트가 끼어들 수 없음).
"""

from __future__ import annotations

import random
import secrets

BODY_COLORS = [
    "#ffb3c7", "#ffc98b", "#ffe17a", "#b9e88a", "#8fdcc2", "#8ccfff",
    "#b3b8ff", "#d4a8ff", "#ff9e9e", "#f7d6b5", "#a8e6e0", "#ffd1f0",
]
BG_COLORS = ["#fff4e0", "#e6f6ff", "#f0ebff", "#e8f8ea", "#fff0f4", "#fffbd6", "#eaf1f7", "#ffeede"]
TOPS = ["none", "cat", "bear", "bunny", "sprout", "antenna", "cap", "visor", "beanie", "flower"]
EYES = ["dot", "happy", "sparkle", "sleepy", "wink"]
MOUTHS = ["smile", "open", "cat", "o", "tongue"]
PATTERNS = ["none", "dimples", "belly", "spots"]

INK = "#3b2f2f"
CHEEK = "#ff8fa3"
PARTS = {"body": BODY_COLORS, "bg": BG_COLORS, "top": TOPS, "eyes": EYES, "mouth": MOUTHS, "pattern": PATTERNS}


def new_avatar(rng: random.Random | None = None) -> dict:
    """부품을 무작위로 고릅니다. 테스트에서는 seed 를 준 Random 을 넘깁니다."""
    rng = rng or secrets.SystemRandom()
    choice = {name: rng.choice(options) for name, options in PARTS.items()}
    choice["cheeks"] = rng.random() < 0.7
    return choice


def is_valid(avatar: dict) -> bool:
    return (
        isinstance(avatar, dict)
        and all(avatar.get(name) in options for name, options in PARTS.items())
        and isinstance(avatar.get("cheeks"), bool)
    )


def _darker(hex_color: str, factor: float = 0.82) -> str:
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return "#{:02x}{:02x}{:02x}".format(*(int(c * factor) for c in (r, g, b)))


def _top(kind: str, body: str) -> str:
    dark = _darker(body)
    if kind == "cat":
        return (f'<path d="M14 26 L17 9 L28 20 Z" fill="{body}"/><path d="M50 26 L47 9 L36 20 Z" fill="{body}"/>'
                f'<path d="M17.5 21 L18.5 13 L24 18.5 Z" fill="{CHEEK}" opacity=".6"/>'
                f'<path d="M46.5 21 L45.5 13 L40 18.5 Z" fill="{CHEEK}" opacity=".6"/>')
    if kind == "bear":
        return (f'<circle cx="16" cy="19" r="7" fill="{body}"/><circle cx="48" cy="19" r="7" fill="{body}"/>'
                f'<circle cx="16" cy="19" r="3.5" fill="{dark}"/><circle cx="48" cy="19" r="3.5" fill="{dark}"/>')
    if kind == "bunny":
        return (f'<ellipse cx="23" cy="14" rx="5" ry="11.5" fill="{body}"/><ellipse cx="41" cy="14" rx="5" ry="11.5" fill="{body}"/>'
                f'<ellipse cx="23" cy="14" rx="2.3" ry="7.5" fill="{CHEEK}" opacity=".55"/>'
                f'<ellipse cx="41" cy="14" rx="2.3" ry="7.5" fill="{CHEEK}" opacity=".55"/>')
    if kind == "sprout":
        return ('<path d="M32 19 C32 14 32 11 32 8" stroke="#4caf50" stroke-width="2.2" fill="none" stroke-linecap="round"/>'
                '<path d="M32 10 C26 4 20 7 21 11 C25 13 29 12 32 10 Z" fill="#6cc66f"/>'
                '<path d="M32 10 C38 4 44 7 43 11 C39 13 35 12 32 10 Z" fill="#83d686"/>')
    if kind == "antenna":
        return (f'<path d="M32 19 L32 8" stroke="{dark}" stroke-width="2" stroke-linecap="round"/>'
                '<circle cx="32" cy="7" r="3.6" fill="#ffd54a"/><circle cx="31" cy="6" r="1.1" fill="#fff"/>')
    if kind == "cap":   # 골프 모자
        return (f'<path d="M13 27 C13 13 51 13 51 27 Z" fill="{INK}" opacity=".88"/>'
                f'<path d="M44 26 C52 25 58 27 59 29 C53 30 47 29 43 28 Z" fill="{INK}"/>'
                '<circle cx="32" cy="15.5" r="2" fill="#fff" opacity=".8"/>')
    if kind == "visor":
        return ('<path d="M12 24 C20 18 44 18 52 24 L52 27 C44 22 20 22 12 27 Z" fill="#ffffff" stroke="#d9d9d9" stroke-width=".8"/>'
                '<path d="M40 24 C50 23 57 25 58 28 C51 29 45 28 40 27 Z" fill="#46a0ff"/>')
    if kind == "beanie":
        return ('<path d="M13 26 C13 10 51 10 51 26 Z" fill="#ff7a8a"/>'
                '<rect x="12" y="23" width="40" height="5" rx="2.5" fill="#ffc1c9"/>'
                '<circle cx="32" cy="9" r="4.2" fill="#ffc1c9"/>')
    if kind == "flower":
        petals = "".join(
            f'<circle cx="{46 + dx}" cy="{17 + dy}" r="3.3" fill="#fff6a8"/>' for dx, dy in ((0, -3.5), (3.3, -1), (2, 3), (-2, 3), (-3.3, -1))
        )
        return petals + '<circle cx="46" cy="17" r="2.4" fill="#ff9f43"/>'
    return ""


def _eyes(kind: str) -> str:
    left, right = 24, 40
    y = 36
    if kind == "happy":
        return "".join(
            f'<path d="M{x - 4} {y + 1} Q{x} {y - 4} {x + 4} {y + 1}" stroke="{INK}" stroke-width="2.4" fill="none" stroke-linecap="round"/>'
            for x in (left, right)
        )
    if kind == "sparkle":
        return "".join(
            f'<circle cx="{x}" cy="{y}" r="4.6" fill="{INK}"/><circle cx="{x + 1.6}" cy="{y - 1.8}" r="1.7" fill="#fff"/>'
            f'<circle cx="{x - 1.6}" cy="{y + 1.6}" r=".9" fill="#fff"/>'
            for x in (left, right)
        )
    if kind == "sleepy":
        return "".join(
            f'<path d="M{x - 4} {y} Q{x} {y + 3.5} {x + 4} {y}" stroke="{INK}" stroke-width="2.4" fill="none" stroke-linecap="round"/>'
            for x in (left, right)
        )
    if kind == "wink":
        return (f'<circle cx="{left}" cy="{y}" r="3.2" fill="{INK}"/><circle cx="{left + 1}" cy="{y - 1.2}" r="1.1" fill="#fff"/>'
                f'<path d="M{right - 4} {y + 1} Q{right} {y - 4} {right + 4} {y + 1}" stroke="{INK}" stroke-width="2.4" fill="none" stroke-linecap="round"/>')
    return "".join(  # dot
        f'<circle cx="{x}" cy="{y}" r="3.2" fill="{INK}"/><circle cx="{x + 1}" cy="{y - 1.2}" r="1.1" fill="#fff"/>' for x in (left, right)
    )


def _mouth(kind: str) -> str:
    if kind == "open":
        return (f'<path d="M27 43 Q32 51 37 43 Z" fill="{INK}"/>'
                '<path d="M29.5 46.5 Q32 49.5 34.5 46.5 Q32 45.5 29.5 46.5 Z" fill="#ff7b8f"/>')
    if kind == "cat":
        return f'<path d="M26.5 43 Q29.2 46.5 32 43 Q34.8 46.5 37.5 43" stroke="{INK}" stroke-width="2" fill="none" stroke-linecap="round"/>'
    if kind == "o":
        return f'<ellipse cx="32" cy="45" rx="2.6" ry="3" fill="{INK}"/>'
    if kind == "tongue":
        return (f'<path d="M27 43 Q32 48 37 43" stroke="{INK}" stroke-width="2" fill="none" stroke-linecap="round"/>'
                '<path d="M30 45.3 Q30 50 32 50 Q34 50 34 45.3 Z" fill="#ff7b8f"/>')
    return f'<path d="M27 43 Q32 48 37 43" stroke="{INK}" stroke-width="2.2" fill="none" stroke-linecap="round"/>'


def _pattern(kind: str, body: str) -> str:
    if kind == "dimples":   # 골프공 딤플
        dots = ((20, 50), (26, 54), (38, 54), (44, 50), (32, 56), (14, 42), (50, 42))
        return "".join(f'<circle cx="{x}" cy="{y}" r="1.4" fill="{_darker(body, .88)}"/>' for x, y in dots)
    if kind == "belly":
        return '<ellipse cx="32" cy="52" rx="10" ry="6" fill="#ffffff" opacity=".55"/>'
    if kind == "spots":
        return (f'<circle cx="16" cy="44" r="3" fill="{_darker(body, .86)}"/><circle cx="49" cy="47" r="2.4" fill="{_darker(body, .86)}"/>'
                f'<circle cx="44" cy="25" r="2" fill="{_darker(body, .86)}"/>')
    return ""


def render_svg(avatar: dict) -> str:
    if not is_valid(avatar):
        raise ValueError("알 수 없는 캐릭터 부품입니다.")
    body, top = avatar["body"], avatar["top"]
    behind = top in {"cat", "bear", "bunny", "sprout", "antenna"}   # 귀·새싹은 몸 뒤, 모자·꽃은 몸 위
    cheeks = (
        f'<ellipse cx="17" cy="42" rx="4" ry="2.6" fill="{CHEEK}" opacity=".55"/>'
        f'<ellipse cx="47" cy="42" rx="4" ry="2.6" fill="{CHEEK}" opacity=".55"/>'
        if avatar["cheeks"] else ""
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">'
        f'<rect width="64" height="64" fill="{avatar["bg"]}"/>'
        + (_top(top, body) if behind else "")
        + f'<ellipse cx="32" cy="40" rx="22" ry="21" fill="{body}"/>'
        + '<ellipse cx="24" cy="27" rx="7" ry="4" fill="#ffffff" opacity=".35"/>'
        + _pattern(avatar["pattern"], body)
        + ("" if behind else _top(top, body))
        + cheeks + _eyes(avatar["eyes"]) + _mouth(avatar["mouth"])
        + "</svg>"
    )
