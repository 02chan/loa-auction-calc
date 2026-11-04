"""앱 아이콘을 코드로 그린다 (이미지 파일을 저장소에 두지 않기 위해).

디자인: 어두운 남색 둥근 사각형 바탕 + 골드 코인 + 경매 망치.
작은 크기(16~32px)에서도 알아볼 수 있도록 큰 도형 위주로 그리고,
4배 크기로 그린 뒤 줄여서 가장자리를 매끄럽게 한다.

    python -m loa_auction.icon build\\icon\\LoaAuctionCalc.ico   # exe 아이콘 만들기 (build.ps1에서 사용)
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
SUPERSAMPLE = 4

BG_TOP = (44, 49, 62)
BG_BOTTOM = (20, 22, 28)
BG_EDGE = (70, 76, 92)
GOLD_LIGHT = (255, 222, 120)
GOLD = (245, 194, 66)
GOLD_DARK = (176, 120, 18)
GAVEL = (246, 242, 234)
GAVEL_SHADE = (196, 188, 172)
OUTLINE = (17, 18, 23)


def _vertical_gradient(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    grad = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / max(1, size - 1)
        grad.putpixel((0, y), tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)))
    return grad.resize((size, size))


def _radial_gold(diameter: int) -> Image.Image:
    """왼쪽 위가 밝은 골드 원판."""
    disc = Image.new("RGB", (diameter, diameter), GOLD)
    px = disc.load()
    cx = cy = diameter * 0.35
    r_max = diameter * 0.95
    for y in range(diameter):
        for x in range(diameter):
            d = min(1.0, ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 / r_max)
            a, b = (GOLD_LIGHT, GOLD) if d < 0.5 else (GOLD, GOLD_DARK)
            t = d / 0.5 if d < 0.5 else (d - 0.5) / 0.5
            px[x, y] = tuple(int(p + (q - p) * t) for p, q in zip(a, b))
    return disc


def _draw(size: int) -> Image.Image:
    """size×size 캔버스(슈퍼샘플 크기)에 아이콘을 그린다."""
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # 1) 둥근 사각형 바탕
    margin = round(s * 0.03)
    radius = round(s * 0.22)
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle((margin, margin, s - margin, s - margin), radius=radius, fill=255)
    img.paste(_vertical_gradient(s, BG_TOP, BG_BOTTOM), (0, 0), mask)
    ImageDraw.Draw(img).rounded_rectangle(
        (margin, margin, s - margin, s - margin), radius=radius, outline=BG_EDGE, width=max(1, round(s * 0.012))
    )

    # 2) 골드 코인 (왼쪽 아래)
    d = round(s * 0.58)
    cx, cy = round(s * 0.39), round(s * 0.61)
    x0, y0 = cx - d // 2, cy - d // 2
    coin_mask = Image.new("L", (d, d), 0)
    ImageDraw.Draw(coin_mask).ellipse((0, 0, d - 1, d - 1), fill=255)
    shadow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse(
        (x0 + s * 0.02, y0 + s * 0.03, x0 + d + s * 0.02, y0 + d + s * 0.03), fill=(0, 0, 0, 110)
    )
    img.alpha_composite(shadow)
    img.paste(_radial_gold(d), (x0, y0), coin_mask)
    draw = ImageDraw.Draw(img)
    draw.ellipse((x0, y0, x0 + d, y0 + d), outline=GOLD_DARK, width=max(1, round(s * 0.022)))
    inset = round(d * 0.16)
    draw.ellipse(
        (x0 + inset, y0 + inset, x0 + d - inset, y0 + d - inset), outline=GOLD_DARK, width=max(1, round(s * 0.018))
    )

    # 3) 경매 망치: 머리는 코인 오른쪽 위, 손잡이는 오른쪽 아래 모서리 쪽(45도)
    #    캔버스 가운데에 똑바로 그린 뒤 회전·이동한다.
    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    g = ImageDraw.Draw(layer)
    ow = max(1, round(s * 0.03))  # 외곽선 두께
    c = s / 2  # 망치 머리 중심(회전 전)
    head_w, head_h = s * 0.44, s * 0.22
    handle_w, handle_len = s * 0.13, s * 0.37
    g.rounded_rectangle(
        (c - handle_w / 2, c, c + handle_w / 2, c + handle_len),
        radius=round(handle_w / 2),
        fill=GAVEL,
        outline=OUTLINE,
        width=ow,
    )
    g.rounded_rectangle(
        (c - head_w / 2, c - head_h / 2, c + head_w / 2, c + head_h / 2),
        radius=round(head_h * 0.30),
        fill=GAVEL,
        outline=OUTLINE,
        width=ow,
    )
    # 머리 양 끝의 띠(망치다운 모양)
    band = head_w * 0.11
    for bx in (c - head_w / 2 + band * 0.9, c + head_w / 2 - band * 1.9):
        g.rectangle((bx, c - head_h / 2 + ow, bx + band, c + head_h / 2 - ow), fill=GAVEL_SHADE)
    head_x, head_y = s * 0.60, s * 0.37  # 회전 후 머리 중심 위치
    layer = layer.rotate(45, resample=Image.BICUBIC, center=(c, c), translate=(head_x - c, head_y - c))
    img.alpha_composite(layer)
    return img


def render_icon(size: int) -> Image.Image:
    """size×size RGBA 아이콘."""
    big = _draw(size * SUPERSAMPLE)
    # 4×4 픽셀 평균으로 정확히 줄인다(LANCZOS처럼 가장자리에 번짐/잔상이 생기지 않음)
    return big.reduce(SUPERSAMPLE)


def write_ico(path: Path | str) -> Path:
    """여러 크기(16~256px)를 담은 .ico 파일을 만든다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    images = [render_icon(n) for n in ICO_SIZES]
    images[-1].save(path, format="ICO", sizes=[(n, n) for n in ICO_SIZES], append_images=images[:-1])
    return path


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("사용법: python -m loa_auction.icon <출력 경로.ico>", file=sys.stderr)
        return 2
    print(write_ico(argv[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
