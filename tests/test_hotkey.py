"""전역 단축키 테스트: 문자열 파서, 실제 RegisterHotKey 등록/해제, WM_HOTKEY 처리."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

import pytest
from PySide6.QtCore import QByteArray

from loa_auction import hotkey
from loa_auction.config import Config, save_config
from loa_auction.history import HistoryStore
from loa_auction.hotkey import (
    ERROR_HOTKEY_ALREADY_REGISTERED,
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    MOD_WIN,
    WM_HOTKEY,
    GlobalHotkey,
    parse_hotkey,
)

# 다른 프로그램과 겹칠 가능성이 낮은 조합들(앞에서부터 비어 있는 것을 쓴다)
CANDIDATES = ["Ctrl+Alt+Shift+F12", "Ctrl+Alt+Shift+F11", "Ctrl+Alt+Shift+F10", "Ctrl+Alt+Shift+F9"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("F8", (0, 0x77)),
        ("F9", (0, 0x78)),
        ("F1", (0, 0x70)),
        ("F24", (0, 0x87)),
        ("Ctrl+Shift+A", (MOD_CONTROL | MOD_SHIFT, 0x41)),
        ("ctrl + shift + a", (MOD_CONTROL | MOD_SHIFT, 0x41)),
        ("Alt+F4", (MOD_ALT, 0x73)),
        ("Win+Z", (MOD_WIN, 0x5A)),
        ("Control+1", (MOD_CONTROL, 0x31)),
        ("Shift+Space", (MOD_SHIFT, 0x20)),
        ("Ctrl+Alt+Shift+F12", (MOD_CONTROL | MOD_ALT | MOD_SHIFT, 0x7B)),
        ("PageUp", (0, 0x21)),
        ("Num5", (0, 0x65)),
    ],
)
def test_parse_hotkey(text, expected):
    assert parse_hotkey(text) == expected


@pytest.mark.parametrize("text", ["", "  ", "Ctrl+", "+A", "Ctrl+Shift", "Hyper+A", "F25", "F0", "AB", "Ctrl++A"])
def test_parse_hotkey_invalid(text):
    with pytest.raises(ValueError):
        parse_hotkey(text)


def register_any(hk: GlobalHotkey) -> str:
    for text in CANDIDATES:
        if hk.register(text):
            return text
    raise AssertionError(f"후보 단축키를 하나도 등록하지 못했습니다(마지막 오류 {hk.last_error})")


def test_register_and_unregister_real_hotkey(qapp):
    hk = GlobalHotkey()
    text = register_any(hk)
    assert hk.is_registered and hk.hotkey_text == text and hk.last_error == 0
    assert hk.unregister() is True
    assert not hk.is_registered
    # 해제 후 다시 등록 가능
    assert hk.register(text) is True
    assert hk.unregister() is True
    assert hk.unregister() is False  # 이미 해제됨


def test_conflicting_registration_fails_with_error_code(qapp):
    first, second = GlobalHotkey(), GlobalHotkey()
    text = register_any(first)
    try:
        assert second.register(text) is False
        assert second.last_error == ERROR_HOTKEY_ALREADY_REGISTERED
        assert not second.is_registered
    finally:
        first.unregister()
    assert second.register(text) is True
    second.unregister()


def make_msg(message: int, wparam: int) -> wintypes.MSG:
    msg = wintypes.MSG()
    msg.message = message
    msg.wParam = wparam
    return msg


@pytest.mark.parametrize("pointer", ["VoidPtr", "int"])
def test_wm_hotkey_message_emits_activated(qtbot, qapp, pointer):
    from shiboken6 import VoidPtr

    hk = GlobalHotkey()
    register_any(hk)
    try:
        event_type = QByteArray(b"windows_generic_MSG")
        msg = make_msg(WM_HOTKEY, hk.hotkey_id)
        # Qt는 MSG 주소를 shiboken VoidPtr로 넘긴다(bool()이 항상 거짓인 객체)
        address = ctypes.addressof(msg)
        message = VoidPtr(address) if pointer == "VoidPtr" else address
        with qtbot.waitSignal(hk.activated, timeout=1000):
            handled, _ = hk._filter.nativeEventFilter(event_type, message)
        assert handled is True
        with qtbot.assertNotEmitted(hk.activated):
            other_id = make_msg(WM_HOTKEY, hk.hotkey_id + 1)
            assert hk._filter.nativeEventFilter(event_type, ctypes.addressof(other_id))[0] is False
            other_msg = make_msg(0x0100, hk.hotkey_id)  # WM_KEYDOWN
            assert hk._filter.nativeEventFilter(event_type, ctypes.addressof(other_msg))[0] is False
    finally:
        hk.unregister()


def _window(qtbot, tmp_path, config: Config):
    from loa_auction.ui import MiniWindow

    path = tmp_path / "config.json"
    save_config(config, path)
    win = MiniWindow(config, path, HistoryStore(tmp_path / "history.csv"))
    qtbot.addWidget(win)
    return win


def test_window_registers_configured_hotkey_and_releases_in_paste_only_mode(qtbot, qapp, tmp_path):
    probe = GlobalHotkey()
    text = register_any(probe)
    probe.unregister()
    win = _window(qtbot, tmp_path, Config(hotkey=text, capture_enabled=True))
    assert win._hotkey is not None and win._hotkey.is_registered
    assert text in win.status_label.text()
    win.paste_only_check.setChecked(True)
    assert not win._hotkey.is_registered
    assert "붙여넣기 전용" in win.status_label.text()
    win.paste_only_check.setChecked(False)
    assert win._hotkey.is_registered
    win.close()
    assert not win._hotkey.is_registered


def test_window_shows_registration_failure(qtbot, qapp, tmp_path):
    holder = GlobalHotkey()
    text = register_any(holder)
    try:
        win = _window(qtbot, tmp_path, Config(hotkey=text, capture_enabled=True))
        assert "등록 실패" in win.status_label.text()
        assert not win._hotkey.is_registered
    finally:
        holder.unregister()


def test_window_shows_invalid_hotkey_text(qtbot, qapp, tmp_path):
    win = _window(qtbot, tmp_path, Config(hotkey="Hyper+Q", capture_enabled=True))
    assert "단축키 설정 오류" in win.status_label.text()


def test_register_uses_win32_wrapper(monkeypatch, qapp):
    calls = []
    monkeypatch.setattr(hotkey, "_register_hotkey", lambda hid, mods, vk: calls.append((mods, vk)) or (True, 0))
    monkeypatch.setattr(hotkey, "_unregister_hotkey", lambda hid: True)
    hk = GlobalHotkey()
    assert hk.register("Ctrl+Shift+A")
    assert calls == [(MOD_CONTROL | MOD_SHIFT, 0x41)]
    hk.unregister()
