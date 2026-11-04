"""미니 창 테스트 (QT_QPA_PLATFORM=offscreen). OCR은 실제 Windows OCR을 쓴다."""

from __future__ import annotations

import json
import threading

import pytest
from conftest import EXPECTED_ITEM_SUBSTRING, expected_price, fixture_params, require_fixture
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

from loa_auction import ocr
from loa_auction.calc import calculate
from loa_auction.capture import pil_to_qimage
from loa_auction.config import Config, load_config, save_config
from loa_auction.history import HistoryStore
from loa_auction.ocr import OcrResult
from loa_auction.ui import FAIL_TEXT, MiniWindow

OCR_TIMEOUT_MS = 30000


@pytest.fixture
def make_window(qtbot, tmp_path):
    config_path = tmp_path / "config.json"
    history_path = tmp_path / "history.csv"
    if not config_path.exists():
        # UI 테스트에서는 실제 전역 단축키를 잡지 않는다(단축키는 test_hotkey/test_safety에서 검증)
        save_config(Config(capture_enabled=False), config_path)
    windows = []

    def factory(ocr_func=None):
        win = MiniWindow(load_config(config_path), config_path, HistoryStore(history_path), ocr_func=ocr_func)
        qtbot.addWidget(win)
        windows.append(win)
        return win

    factory.config_path = config_path
    factory.history_path = history_path
    yield factory
    for win in windows:  # 진행 중인 OCR이 끝난 뒤 정리
        qtbot.waitUntil(lambda w=win: not w.busy, timeout=OCR_TIMEOUT_MS)


def put_image_on_clipboard(img: Image.Image) -> None:
    QGuiApplication.clipboard().setImage(pil_to_qimage(img))


def fmt(v: int) -> str:
    return f"{v:,}"


def test_window_is_always_on_top_and_small(make_window):
    win = make_window()
    assert win.isWindow()
    assert win.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert win.width() <= 360 and win.height() <= 300


def test_party_toggle_changes_result(make_window):
    win = make_window()
    win.price_edit.setText("7776")
    win.btn4.click()
    assert win.btn4.isChecked() and not win.btn8.isChecked()
    assert win.bid_label.text() == "5,036"
    assert win.sell_label.text().endswith("5,540")
    assert win.use_label.text().endswith("5,832")
    win.btn8.click()
    assert win.btn8.isChecked() and not win.btn4.isChecked()
    assert win.bid_label.text() == "5,876"
    assert win.sell_label.text().endswith("6,463")
    assert win.use_label.text().endswith("6,804")


def test_price_edit_recalculates_immediately(qtbot, make_window):
    win = make_window()
    win.btn4.click()
    assert win.bid_label.text() == "-"
    qtbot.keyClicks(win.price_edit, "7776")
    assert win.bid_label.text() == "5,036"
    qtbot.keyClick(win.price_edit, Qt.Key.Key_Backspace)  # 777
    assert win.bid_label.text() == fmt(calculate(777, 4).recommended_bid)
    qtbot.keyClicks(win.price_edit, "abc")  # 숫자만 입력된다
    assert win.price_edit.text() == "777"
    win.price_edit.clear()
    assert win.bid_label.text() == "-"


def test_party_size_saved_and_restored(make_window):
    win = make_window()
    win.btn8.click()
    assert json.loads(make_window.config_path.read_text("utf-8"))["last_party_size"] == 8
    win2 = make_window()
    assert win2.btn8.isChecked() and win2.party_size == 8
    win2.btn4.click()
    assert load_config(make_window.config_path).last_party_size == 4
    assert make_window().btn4.isChecked()


def test_ocr_failure_shows_message_and_clears_price(qtbot, make_window):
    win = make_window()
    win.price_edit.setText("1234")
    put_image_on_clipboard(Image.new("RGB", (1280, 720), (12, 14, 20)))  # 경매 화면이 아닌 이미지
    qtbot.keyClick(win, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(lambda: win.item_label.text() == FAIL_TEXT, timeout=OCR_TIMEOUT_MS)
    assert win.price_edit.text() == ""
    assert win.bid_label.text() == "-"
    assert FAIL_TEXT in win.status_label.text()
    assert HistoryStore(make_window.history_path).rows() == []


@pytest.mark.parametrize("target", ["window", "price_edit"])
@pytest.mark.parametrize("clipboard_text", ["이미지 아님", ""])
def test_paste_without_image_shows_hint(qtbot, make_window, target, clipboard_text):
    win = make_window()
    win.show()
    QGuiApplication.clipboard().clear()
    if clipboard_text:
        QGuiApplication.clipboard().setText(clipboard_text)
    widget = win if target == "window" else win.price_edit
    win.price_edit.setText("123")
    qtbot.keyClick(widget, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert "클립보드에 이미지가 없습니다" in win.status_label.text()
    assert win.price_edit.text() == "123"  # 글자가 가격 칸에 붙지 않는다
    assert not win.busy


def test_paste_digit_text_into_price_edit(qtbot, make_window):
    win = make_window()
    win.btn4.click()
    QGuiApplication.clipboard().setText("7,776")
    qtbot.keyClick(win.price_edit, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert win.price_edit.text() == "7776"
    assert win.bid_label.text() == "5,036"
    assert not win.busy


@pytest.mark.parametrize("path", fixture_params())
@pytest.mark.parametrize("target", ["window", "price_edit"])
def test_ctrl_v_fixture_fills_price_and_bid(qtbot, make_window, path, target):
    path = require_fixture(path)
    win = make_window()
    price = expected_price(path)
    with Image.open(path) as im:
        put_image_on_clipboard(im)
    widget = win if target == "window" else win.price_edit
    if target == "price_edit":
        win.price_edit.setFocus()
    qtbot.keyClick(widget, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(lambda: not win.busy and win.price_edit.text() != "", timeout=OCR_TIMEOUT_MS)
    assert win.price_edit.text() in (str(price), fmt(price))
    assert win.bid_label.text() == fmt(calculate(price, win.party_size).recommended_bid)
    expected_item = EXPECTED_ITEM_SUBSTRING.get(path.name)
    if expected_item:
        assert expected_item in win.item_label.text()
    rows = HistoryStore(make_window.history_path).rows()
    assert len(rows) == 1
    assert rows[0].price == price and rows[0].party_size == win.party_size
    assert rows[0].recommended_bid == calculate(price, win.party_size).recommended_bid
    assert rows[0].actual_price is None


def test_ocr_runs_off_the_ui_thread(qtbot, make_window):
    seen = {}

    def recording_ocr(img):
        seen["thread"] = threading.get_ident()
        return ocr.recognize(img)

    win = make_window(ocr_func=recording_ocr)
    put_image_on_clipboard(Image.new("RGB", (640, 360)))
    qtbot.keyClick(win, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert win.busy  # 이벤트 루프로 바로 돌아온다
    qtbot.waitUntil(lambda: not win.busy, timeout=OCR_TIMEOUT_MS)
    assert seen["thread"] != threading.get_ident()


def test_history_panel_shows_recent_10_and_saves_actual_price(qtbot, make_window):
    store = HistoryStore(make_window.history_path)
    from datetime import datetime, timedelta

    t0 = datetime(2026, 11, 4, 20, 0, 0)
    for i in range(12):
        store.append(f"아이템{i}", 1000 + i, 4, 600 + i, now=t0 + timedelta(seconds=10 * i))
    win = make_window()
    assert not win.history_table.isVisible()
    win.show()
    win.history_toggle.click()
    assert win.history_table.isVisible()
    table = win.history_table
    assert table.rowCount() == 10
    assert table.item(0, win.COL_ITEM).text() == "아이템11"  # 최신이 위
    assert table.item(9, win.COL_ITEM).text() == "아이템2"
    # 실제 낙찰가 칸만 편집 가능
    assert table.item(0, win.COL_ACTUAL).flags() & Qt.ItemFlag.ItemIsEditable
    assert not table.item(0, win.COL_PRICE).flags() & Qt.ItemFlag.ItemIsEditable
    table.item(1, win.COL_ACTUAL).setText("950")
    rows = HistoryStore(make_window.history_path).rows()
    assert rows[10].actual_price == 950  # 두 번째 최신 = 파일의 11번째 행
    assert all(r.actual_price is None for i, r in enumerate(rows) if i != 10)
    assert win.history_table.item(1, win.COL_ACTUAL).text() == "950"
    # 숫자가 아니면 저장하지 않는다
    table.item(1, win.COL_ACTUAL).setText("abc")
    assert HistoryStore(make_window.history_path).rows()[10].actual_price == 950
    win.history_toggle.click()
    assert not win.history_table.isVisible()


def test_editing_actual_price_while_new_result_arrives_saves_to_right_row(qtbot, make_window):
    from datetime import datetime, timedelta

    from PySide6.QtWidgets import QApplication, QLineEdit

    store = HistoryStore(make_window.history_path)
    t0 = datetime(2026, 11, 4, 20, 0, 0)
    for i in range(3):
        store.append(f"item{i}", 1000 + i, 4, 600 + i, now=t0 + timedelta(seconds=10 * i))
    win = make_window()
    win.show()
    win.history_toggle.click()
    target = win.history_table.item(1, win.COL_ACTUAL)  # item1
    assert win.history_table.item(1, win.COL_ITEM).text() == "item1"
    win.history_table.setCurrentItem(target)
    win.history_table.editItem(target)
    editor = win.history_table.findChild(QLineEdit)
    assert editor is not None
    qtbot.keyClicks(editor, "999")
    # 편집 중에 새 인식 결과가 들어와 표가 한 칸씩 밀리는 상황
    win._busy = True
    win._on_ocr_finished(OcrResult("NEW", 5555, True))
    qtbot.keyClick(editor, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: win.history_table.state() != win.history_table.State.EditingState, timeout=2000)
    QApplication.processEvents()
    rows = {r.item_name: r for r in HistoryStore(make_window.history_path).rows()}
    assert rows["item1"].actual_price == 999
    assert rows["NEW"].actual_price is None
    qtbot.waitUntil(lambda: win.history_table.item(0, win.COL_ITEM).text() == "NEW", timeout=2000)


def test_history_write_failure_is_reported(qtbot, tmp_path, make_window):
    win = make_window()
    locked = tmp_path / "locked"
    locked.mkdir()
    win.history = HistoryStore(locked)  # 폴더라서 파일로 열 수 없다(엑셀 잠금과 같은 OSError)
    win._busy = True
    win._on_ocr_finished(OcrResult("유물 전율 각인서", 7776, True))
    assert win.price_edit.text() == "7776"
    assert win.bid_label.text() == fmt(calculate(7776, win.party_size).recommended_bid)
    assert "기록 저장 실패" in win.status_label.text()
    assert not win.busy


def test_window_starts_with_cp949_history(make_window):
    make_window.history_path.write_bytes(
        "시각,아이템명,거래소최저가,인원,추천입찰가,실제낙찰가\r\n2026-11-04 20:00:00,전율 각인서,7776,4,5036,\r\n".encode("cp949")
    )
    win = make_window()
    assert win.history_table.rowCount() == 1
    assert win.history_table.item(0, win.COL_ITEM).text() == "전율 각인서"


def test_window_position_saved_on_close(make_window):
    win = make_window()
    win.move(123, 77)
    win.close()
    assert load_config(make_window.config_path).window_pos == [win.x(), win.y()]
