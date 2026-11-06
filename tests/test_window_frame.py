"""테두리 없는 창, 닫기/접기 버튼, 아이콘 미니 모드 테스트 (offscreen)."""

from __future__ import annotations

from collections import Counter

import pytest
from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor

from loa_auction import capture, hotkey
from loa_auction.config import Config, load_config, save_config
from loa_auction.history import HistoryStore
from loa_auction.ocr import OcrResult
from loa_auction.ui import WINDOW_BG, IconBubble, MiniWindow


@pytest.fixture
def make_window(qtbot, tmp_path):
    path = tmp_path / "config.json"

    def factory(config: Config | None = None) -> MiniWindow:
        cfg = config or Config(capture_enabled=False)
        save_config(cfg, path)
        win = MiniWindow(load_config(path), path, HistoryStore(tmp_path / "history.csv"))
        qtbot.addWidget(win)
        qtbot.addWidget(win.bubble)
        return win

    factory.config_path = path
    return factory


def test_window_has_no_title_bar_and_stays_on_top(make_window):
    win = make_window()
    flags = win.windowFlags()
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert win.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert win.close_button.text() == "✕" and win.collapse_button.text() == "—"


def test_rounded_background_is_painted(qtbot, make_window):
    win = make_window()
    win.show()
    image = win.grab().toImage()
    assert image.pixelColor(0, 0).alpha() == 0  # 둥근 모서리 바깥은 투명
    center = image.pixelColor(image.width() // 2, image.height() - 3)
    assert center.alpha() == 255
    assert abs(center.red() - QColor(WINDOW_BG).red()) < 6


def test_item_name_row_is_drag_handle_not_text_selection(make_window):
    win = make_window()
    assert win.item_label.textInteractionFlags() == Qt.TextInteractionFlag.NoTextInteraction
    assert win.item_label.parentWidget() is win.header


def test_dragging_header_moves_window(qtbot, make_window):
    win = make_window()
    win.show()
    win.move(100, 100)
    start = win.pos()
    qtbot.mousePress(win.header, Qt.MouseButton.LeftButton, pos=QPoint(60, 5))
    qtbot.mouseMove(win.header, QPoint(100, 25))
    qtbot.mouseRelease(win.header, Qt.MouseButton.LeftButton, pos=QPoint(100, 25))
    assert win.pos() == start + QPoint(40, 20)


def test_close_button_saves_and_asks_app_to_quit(qtbot, make_window):
    win = make_window()
    win.show()
    win.move(150, 90)
    with qtbot.waitSignal(win.exitRequested, timeout=1000):
        qtbot.mouseClick(win.close_button, Qt.MouseButton.LeftButton)
    assert not win.isVisible()
    assert load_config(make_window.config_path).window_pos == [150, 90]


def test_collapse_shows_small_icon_in_place(qtbot, make_window):
    win = make_window()
    win.show()
    win.move(200, 120)
    qtbot.mouseClick(win.collapse_button, Qt.MouseButton.LeftButton)
    assert win.collapsed
    assert not win.isVisible()
    bubble = win.bubble
    assert bubble.isVisible()
    assert bubble.pos() == QPoint(200, 120)
    assert bubble.size().width() == IconBubble.SIZE == bubble.size().height()
    flags = bubble.windowFlags()
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert flags & Qt.WindowType.FramelessWindowHint
    assert (flags & Qt.WindowType.Tool) == Qt.WindowType.Tool  # 작업 표시줄에 따로 나오지 않음


def test_clicking_icon_restores_window_with_collapse_button(qtbot, make_window):
    win = make_window()
    win.show()
    win.move(200, 120)
    win.collapse()
    qtbot.mouseClick(win.bubble, Qt.MouseButton.LeftButton)
    assert not win.collapsed
    assert win.isVisible() and not win.bubble.isVisible()
    assert win.pos() == QPoint(200, 120)
    assert win.collapse_button.isVisible()  # 다시 접을 수 있다
    win.collapse_button.click()
    assert win.collapsed and win.bubble.isVisible()


def test_dragging_icon_moves_it_without_expanding(qtbot, make_window):
    win = make_window()
    win.show()
    win.move(200, 120)
    win.collapse()
    bubble = win.bubble
    qtbot.mousePress(bubble, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
    qtbot.mouseMove(bubble, QPoint(60, 50))
    qtbot.mouseRelease(bubble, Qt.MouseButton.LeftButton, pos=QPoint(60, 50))
    assert win.collapsed  # 끌기는 클릭이 아니다
    assert bubble.pos() == QPoint(240, 150)
    qtbot.mouseClick(bubble, Qt.MouseButton.LeftButton)
    assert not win.collapsed
    assert win.pos() == win._fit_on_screen(QPoint(240, 150), win.width(), win.height())


def test_expand_keeps_window_on_screen(make_window):
    win = make_window()
    win.show()
    win.collapse()
    screen = win.screen().availableGeometry()
    win.bubble.move(screen.right() - 10, screen.bottom() - 10)  # 화면 오른쪽 아래 구석
    win.expand()
    assert screen.contains(win.frameGeometry())


def test_icon_menu_has_expand_and_exit(qtbot, make_window):
    win = make_window()
    win.show()
    win.move(210, 130)
    win.collapse()
    menu = win.bubble.build_menu()
    actions = {a.text(): a for a in menu.actions() if a.text()}
    assert set(actions) == {"펼치기", "종료"}
    actions["펼치기"].trigger()
    assert not win.collapsed
    win.collapse()
    with qtbot.waitSignal(win.exitRequested, timeout=1000):
        win.bubble.build_menu().actions()[-1].trigger()  # 종료
    assert not win.bubble.isVisible() and not win.isVisible()
    assert load_config(make_window.config_path).window_pos == [210, 130]  # 아이콘 위치 저장


def _corner(win) -> QPoint:
    area = win.screen().availableGeometry()
    return QPoint(area.right() - IconBubble.SIZE + 1, area.bottom() - IconBubble.SIZE + 1)


def test_icon_returns_to_user_placed_corner_after_expand(make_window):
    """아이콘을 화면 구석에 두고 펼쳤다가 다시 접으면, 창 왼쪽 위가 아니라 그 구석으로 돌아간다."""
    win = make_window()
    win.show()
    win.move(200, 120)
    win.collapse()
    corner = _corner(win)
    win.bubble.move(corner)  # 사용자가 아이콘을 구석으로 끌어다 놓음
    win.expand()
    assert win.pos() != corner  # 창은 화면 안에 들어오도록 왼쪽 위로 밀려서 열림
    win.collapse()
    assert win.bubble.pos() == corner
    # 여러 번 반복해도 같은 자리
    win.expand()
    win.move(300, 200)  # 펼친 창을 다른 곳으로 옮겨도
    win.collapse()
    assert win.bubble.pos() == corner


def test_icon_follows_window_until_user_moves_it(make_window):
    win = make_window()
    win.show()
    win.move(200, 120)
    win.collapse()
    win.expand()
    win.move(400, 300)
    win.collapse()
    assert win.bubble.pos() == QPoint(400, 300)  # 아이콘을 옮긴 적이 없으면 창 왼쪽 위
    assert win.config.icon_pos is None


def test_icon_position_is_saved_and_restored_after_restart(qtbot, make_window, tmp_path):
    win = make_window()
    win.show()
    win.move(200, 120)
    win.collapse()
    win.bubble.move(QPoint(330, 210))  # 사용자가 옮김
    win.exit_app()  # 접힌 채로 종료
    saved = load_config(make_window.config_path)
    assert saved.icon_pos == [330, 210]
    assert saved.window_pos == [200, 120]  # 창 위치는 따로 저장
    again = MiniWindow(saved, make_window.config_path, HistoryStore(tmp_path / "history.csv"))
    qtbot.addWidget(again)
    qtbot.addWidget(again.bubble)
    again.show()
    assert again.pos() == QPoint(200, 120)
    again.collapse()
    assert again.bubble.pos() == QPoint(330, 210)


def test_offscreen_saved_icon_position_falls_back_to_window(make_window):
    win = make_window(Config(capture_enabled=False, icon_pos=[-50000, -50000]))  # 없어진 모니터
    win.show()
    win.move(200, 120)
    win.collapse()
    assert win.bubble.pos() == QPoint(200, 120)


def test_program_placement_is_not_remembered_as_user_move(make_window):
    win = make_window()
    win.show()
    win.move(200, 120)
    win.collapse()  # 프로그램이 아이콘을 놓음(place)
    win.bubble.hide()
    win.bubble.show()  # 다시 표시될 때 생기는 이동 이벤트
    assert win.config.icon_pos is None


def test_icon_menu_is_dark_and_readable(make_window):
    win = make_window()
    win.collapse()
    menu = win.bubble.build_menu()
    menu.adjustSize()
    image = menu.grab().toImage()
    background = image.pixelColor(image.width() - 4, image.height() // 2)
    assert background.lightness() < 80  # 흰 글자가 보이는 어두운 바탕(창과 같은 색 계열)


def test_result_while_collapsed_opens_window(make_window):
    win = make_window()
    win.show()
    win.collapse()
    win._busy = True
    win._on_ocr_finished(OcrResult("유물 결투의 대가 각인서", 17500, True))
    assert not win.collapsed and win.isVisible()
    assert win.bid_label.text() == "11,335"


def test_hotkey_still_works_while_collapsed(qtbot, make_window, monkeypatch):
    calls = Counter()
    monkeypatch.setattr(hotkey, "_register_hotkey", lambda *a: calls.update(["register"]) or (True, 0))
    monkeypatch.setattr(hotkey, "_unregister_hotkey", lambda *a: True)

    def fake_grab():
        calls["grab"] += 1
        return [Image.new("RGB", (640, 360), (5, 5, 5))]

    monkeypatch.setattr(capture, "grab_screens", fake_grab)
    win = make_window(Config(capture_enabled=True))
    win.show()
    win.collapse()
    qtbot.wait(300)
    assert calls["grab"] == 0  # 접혀 있어도 저절로 캡처하지 않는다
    win._hotkey.activated.emit()  # F8
    assert calls["grab"] == 1
    qtbot.waitUntil(lambda: not win.busy, timeout=30000)
    assert not win.collapsed and win.isVisible()  # 결과(여기서는 인식 실패)를 보여 주려고 펼침


# ---------------------------------------------------------------- [캡처] 버튼 · 여러 모니터


def _capture_window(make_window, monkeypatch, screens):
    calls = Counter()
    monkeypatch.setattr(hotkey, "_register_hotkey", lambda *a: (True, 0))
    monkeypatch.setattr(hotkey, "_unregister_hotkey", lambda *a: True)

    def fake_grab():
        calls["grab"] += 1
        return screens()

    monkeypatch.setattr(capture, "grab_screens", fake_grab)
    win = make_window(Config(capture_enabled=True))
    win.show()
    return win, calls


def test_capture_button_captures_once_and_recognizes_other_monitor(qtbot, make_window, monkeypatch):
    """게임이 주 모니터가 아닌 곳에 있어도: 첫 화면(빈 화면)에서 실패하면 다음 모니터 화면을 읽는다."""
    from conftest import expected_price, fixture_params, require_fixture

    path = require_fixture(fixture_params()[0].values[0])
    with Image.open(path) as im:
        game = im.convert("RGB")
    win, calls = _capture_window(make_window, monkeypatch, lambda: [Image.new("RGB", (1920, 1080), (8, 8, 12)), game])
    assert win.capture_button.isVisible()
    qtbot.mouseClick(win.capture_button, Qt.MouseButton.LeftButton)
    assert calls["grab"] == 1
    qtbot.waitUntil(lambda: not win.busy, timeout=30000)
    assert win.price_edit.text() == str(expected_price(path))
    qtbot.wait(200)
    assert calls["grab"] == 1  # 버튼 1번 = 캡처 1번


def test_capture_button_hidden_and_inactive_in_paste_only_mode(make_window, monkeypatch):
    win, calls = _capture_window(make_window, monkeypatch, lambda: [Image.new("RGB", (64, 64))])
    win.paste_only_check.setChecked(True)
    assert win.capture_button.isHidden()
    win._on_capture_clicked()
    assert calls["grab"] == 0
    win.paste_only_check.setChecked(False)
    assert win.capture_button.isVisible()


def test_grab_screens_puts_primary_monitor_first(monkeypatch):
    import mss

    grabbed = []

    class FakeShot:
        def __init__(self, mon):
            self.size = (mon["width"], mon["height"])
            self.bgra = bytes(mon["width"] * mon["height"] * 4)

    class FakeScreens:
        monitors = [
            {"left": -1920, "top": 0, "width": 3840, "height": 1080},
            {"left": -1920, "top": 0, "width": 4, "height": 2},  # 왼쪽 보조 모니터
            {"left": 0, "top": 0, "width": 6, "height": 3, "is_primary": True},
        ]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def grab(self, mon):
            grabbed.append(mon["width"])
            return FakeShot(mon)

    monkeypatch.setattr(mss, "MSS", FakeScreens)
    images = capture.grab_screens()
    assert [im.size for im in images] == [(6, 3), (4, 2)]  # 주 모니터가 먼저
    assert sorted(grabbed) == [4, 6]  # 모든 모니터를 한 번씩
