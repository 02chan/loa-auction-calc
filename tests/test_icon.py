"""앱 아이콘(코드로 그림) 테스트."""

from __future__ import annotations

from PIL import Image

from loa_auction import icon
from loa_auction.icon import ICO_SIZES, render_icon, write_ico


def _pixels(im: Image.Image) -> list:
    getter = getattr(im, "get_flattened_data", None) or im.getdata  # Pillow 13+ / 이전 버전
    return list(getter())


def _is_goldish(rgba) -> bool:
    r, g, b, a = rgba
    return a > 200 and r > 180 and g > 120 and b < 120 and r - b > 90


def _is_whiteish(rgba) -> bool:
    r, g, b, a = rgba
    return a > 200 and min(r, g, b) > 200


def test_render_sizes_and_transparent_corners():
    for n in ICO_SIZES:
        im = render_icon(n)
        assert im.size == (n, n) and im.mode == "RGBA"
        assert im.getpixel((0, 0))[3] == 0  # 둥근 모서리 바깥은 투명
        assert im.getpixel((n // 2, n // 2))[3] == 255


def test_icon_has_gold_coin_and_white_gavel():
    im = render_icon(256)
    pixels = _pixels(im)
    gold = sum(_is_goldish(p) for p in pixels) / len(pixels)
    white = sum(_is_whiteish(p) for p in pixels) / len(pixels)
    assert gold > 0.12  # 코인
    assert white > 0.03  # 망치
    assert _is_goldish(im.getpixel((int(256 * 0.30), int(256 * 0.70))))  # 코인 왼쪽 아래 부분


def test_small_icon_still_shows_coin_and_gavel():
    im = render_icon(16)
    pixels = _pixels(im)
    assert any(_is_goldish(p) for p in pixels)
    assert any(min(p[:3]) > 170 and p[3] > 200 for p in pixels)


def test_write_ico_contains_all_sizes(tmp_path):
    path = write_ico(tmp_path / "sub" / "app.ico")
    with Image.open(path) as ico:
        assert ico.format == "ICO"
        assert set(ico.ico.sizes()) == {(n, n) for n in ICO_SIZES}
        for n in ICO_SIZES:
            frame = ico.ico.getimage((n, n))
            assert frame.size == (n, n)


def test_cli_writes_ico(tmp_path, capsys):
    target = tmp_path / "LoaAuctionCalc.ico"
    assert icon.main([str(target)]) == 0
    assert target.is_file() and target.stat().st_size > 1000
    assert icon.main([]) == 2


def test_window_uses_app_icon(qtbot, tmp_path):
    from loa_auction.config import Config, save_config
    from loa_auction.history import HistoryStore
    from loa_auction.ui import WINDOW_ICON_SIZES, MiniWindow

    path = tmp_path / "config.json"
    save_config(Config(capture_enabled=False), path)
    win = MiniWindow(Config(capture_enabled=False), path, HistoryStore(tmp_path / "history.csv"))
    qtbot.addWidget(win)
    win_icon = win.windowIcon()
    assert not win_icon.isNull()
    sizes = {(s.width(), s.height()) for s in win_icon.availableSizes()}
    assert {(n, n) for n in WINDOW_ICON_SIZES} <= sizes
