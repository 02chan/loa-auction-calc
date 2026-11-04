"""Win32 RegisterHotKey 기반 전역 단축키.

- 키보드 후킹(keyboard/pynput, 저수준 훅)을 쓰지 않는다. OS가 등록된 조합을 눌렀을 때
  이 프로세스의 메시지 큐로 WM_HOTKEY를 보내 주는 방식이다.
- 게임 창이나 다른 프로세스에는 접근하지 않는다(hWnd=NULL, 현재 스레드에 등록).
- Qt 이벤트 루프가 받는 메시지는 QAbstractNativeEventFilter로 확인한다.
"""

from __future__ import annotations

import ctypes
import itertools
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Signal

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
ERROR_HOTKEY_ALREADY_REGISTERED = 1409

_MODIFIERS = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "shift": MOD_SHIFT,
    "alt": MOD_ALT,
    "win": MOD_WIN,
    "windows": MOD_WIN,
}

_NAMED_KEYS = {
    "space": 0x20,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "backspace": 0x08,
    "insert": 0x2D,
    "ins": 0x2D,
    "delete": 0x2E,
    "del": 0x2E,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pgup": 0x21,
    "pagedown": 0x22,
    "pgdn": 0x22,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "printscreen": 0x2C,
    "prtsc": 0x2C,
    "pause": 0x13,
    "scrolllock": 0x91,
}


def _parse_key(name: str) -> int:
    key = name.strip().lower().replace(" ", "")
    if key in _NAMED_KEYS:
        return _NAMED_KEYS[key]
    if key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        return 0x70 + int(key[1:]) - 1  # VK_F1 = 0x70
    for prefix in ("numpad", "num"):
        if key.startswith(prefix) and key[len(prefix) :].isdigit() and len(key) == len(prefix) + 1:
            return 0x60 + int(key[-1])  # VK_NUMPAD0 = 0x60
    if len(key) == 1 and ("a" <= key <= "z" or "0" <= key <= "9"):
        return ord(key.upper())  # VK_A..VK_Z, VK_0..VK_9 는 ASCII와 같다
    raise ValueError(f"알 수 없는 키: {name!r}")


def parse_hotkey(text: str) -> tuple[int, int]:
    """"F8", "Ctrl+Shift+A" 같은 문자열 → (modifiers, virtual-key code)."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("단축키가 비어 있습니다")
    parts = [p.strip() for p in text.split("+")]
    if any(not p for p in parts):
        raise ValueError(f"단축키 형식이 올바르지 않습니다: {text!r}")
    modifiers = 0
    for part in parts[:-1]:
        mod = _MODIFIERS.get(part.lower())
        if mod is None:
            raise ValueError(f"알 수 없는 수정키: {part!r}")
        modifiers |= mod
    if parts[-1].lower() in _MODIFIERS:
        raise ValueError(f"수정키만으로는 단축키를 만들 수 없습니다: {text!r}")
    return modifiers, _parse_key(parts[-1])


# --- Win32 호출 (얇은 래퍼: 테스트에서 호출 횟수를 셀 수 있도록 모듈 함수로 둔다) ---------

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
_user32.RegisterHotKey.restype = wintypes.BOOL
_user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.UnregisterHotKey.restype = wintypes.BOOL


def _register_hotkey(hotkey_id: int, modifiers: int, vk: int) -> tuple[bool, int]:
    ok = bool(_user32.RegisterHotKey(None, hotkey_id, modifiers | MOD_NOREPEAT, vk))
    return ok, (0 if ok else ctypes.get_last_error())


def _unregister_hotkey(hotkey_id: int) -> bool:
    return bool(_user32.UnregisterHotKey(None, hotkey_id))


_ids = itertools.count(0xA000)  # 앱 정의 hotkey id 범위 0x0000~0xBFFF


class _NativeFilter(QAbstractNativeEventFilter):
    def __init__(self, owner: "GlobalHotkey"):
        super().__init__()
        self._owner = owner

    def nativeEventFilter(self, eventType, message):  # noqa: N802 - Qt 시그니처
        # message는 shiboken VoidPtr라서 bool()이 항상 거짓이다. 주소(int)로 바꿔서 본다.
        if bytes(eventType) == b"windows_generic_MSG" and message is not None:
            address = int(message)
            if address and self._owner.handle_native_message(address):
                return True, 0
        return False, 0


class GlobalHotkey(QObject):
    """전역 단축키 1개. 눌리면 activated 시그널을 낸다."""

    activated = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._filter = _NativeFilter(self)
        self._filter_installed = False
        self.hotkey_id: int | None = None
        self.hotkey_text = ""
        self.last_error = 0

    @property
    def is_registered(self) -> bool:
        return self.hotkey_id is not None

    def register(self, text: str) -> bool:
        """단축키를 등록한다. 형식 오류는 ValueError, 다른 프로그램과 충돌하면 False."""
        modifiers, vk = parse_hotkey(text)
        self.unregister()
        hotkey_id = next(_ids)
        ok, err = _register_hotkey(hotkey_id, modifiers, vk)
        self.last_error = err
        if not ok:
            return False
        self.hotkey_id, self.hotkey_text = hotkey_id, text
        app = QCoreApplication.instance()
        if app is not None and not self._filter_installed:
            app.installNativeEventFilter(self._filter)
            self._filter_installed = True
        return True

    def unregister(self) -> bool:
        if self._filter_installed:
            app = QCoreApplication.instance()
            if app is not None:
                app.removeNativeEventFilter(self._filter)
            self._filter_installed = False
        if self.hotkey_id is None:
            return False
        ok = _unregister_hotkey(self.hotkey_id)
        self.hotkey_id, self.hotkey_text = None, ""
        return ok

    def handle_native_message(self, address: int) -> bool:
        """Qt가 넘겨준 MSG 구조체 주소를 보고 우리 단축키면 activated를 낸다."""
        if self.hotkey_id is None:
            return False
        msg = wintypes.MSG.from_address(address)
        if msg.message == WM_HOTKEY and msg.wParam == self.hotkey_id:
            self.activated.emit()
            return True
        return False
