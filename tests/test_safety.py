"""운영정책 안전 조항 검사.

(a) 소스 전체에 금지 API/네트워크 문자열이 없다. (이 파일만 검사에서 제외)
(b) 붙여넣기 전용 모드(capture_enabled=false)에서는 단축키 등록·mss 캡처 함수가 호출되지 않는다.
(c) 화면 캡처 함수는 단축키 콜백에서만 호출되고, 타이머·루프에서는 호출되지 않는다.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

import mss
import pytest
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

from loa_auction import capture, hotkey
from loa_auction.capture import pil_to_qimage
from loa_auction.config import Config, save_config
from loa_auction.history import HistoryStore

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "loa_auction"
THIS_FILE = Path(__file__).resolve()

FORBIDDEN = [
    "OpenProcess",
    "ReadProcessMemory",
    "WriteProcessMemory",
    "SendInput",
    "keybd_event",
    "mouse_event",
    "PostMessage",
    "SendMessage",
    "SetWindowsHookEx",
    "FindWindow",
    "CreateRemoteThread",
    "requests",
    "urllib",
    "socket",
    "http.client",
]


def scanned_files() -> list[Path]:
    patterns = ["*.py", "*.ps1", "*.md", "*.txt", "*.ini", "*.toml", "*.cfg", "*.spec"]
    skip_dirs = {".venv", "build", "dist", "__pycache__", ".git", ".omc", ".pytest_cache"}
    files = []
    for pattern in patterns:
        for path in ROOT.rglob(pattern):
            if skip_dirs.intersection(path.relative_to(ROOT).parts):
                continue
            if path.resolve() == THIS_FILE:
                continue
            files.append(path)
    return sorted(set(files))


def package_sources() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(PACKAGE.glob("*.py"))}


# ---------------------------------------------------------------------- (a)
def test_scan_covers_all_sources():
    names = {p.relative_to(ROOT).as_posix() for p in scanned_files()}
    for required in [
        "loa_auction/calc.py",
        "loa_auction/ocr.py",
        "loa_auction/capture.py",
        "loa_auction/hotkey.py",
        "loa_auction/history.py",
        "loa_auction/config.py",
        "loa_auction/ui.py",
        "loa_auction/__main__.py",
        "tests/test_ui.py",
        "build.ps1",
        "README.md",
    ]:
        assert required in names
    assert "tests/test_safety.py" not in names


@pytest.mark.parametrize("word", FORBIDDEN)
def test_no_forbidden_api_strings(word):
    hits = [
        f"{p.relative_to(ROOT)}:{n}"
        for p in scanned_files()
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
        if word in line
    ]
    assert hits == [], f"금지 문자열 {word!r} 발견: {hits}"


def test_no_low_level_input_or_hook_libraries():
    for name, text in package_sources().items():
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
            else:
                continue
            for mod in mods:
                root = mod.split(".")[0]
                assert root not in {"keyboard", "pynput", "pyautogui", "win32api", "win32gui", "pymem"}, (name, mod)


# ---------------------------------------------------------------------- (b)
@pytest.fixture
def counters(monkeypatch):
    calls = Counter()

    def fake_register(hotkey_id, modifiers, vk):
        calls["register"] += 1
        return True, 0

    def fake_unregister(hotkey_id):
        calls["unregister"] += 1
        return True

    def fake_grab():
        calls["grab"] += 1
        return Image.new("RGB", (800, 450), (5, 5, 5))

    def fake_mss(*args, **kwargs):
        calls["mss"] += 1
        raise AssertionError("mss가 호출되면 안 됩니다")

    monkeypatch.setattr(hotkey, "_register_hotkey", fake_register)
    monkeypatch.setattr(hotkey, "_unregister_hotkey", fake_unregister)
    monkeypatch.setattr(capture, "grab_primary_monitor", fake_grab)
    monkeypatch.setattr(mss, "mss", fake_mss)
    monkeypatch.setattr(mss, "MSS", fake_mss)
    return calls


def make_window(qtbot, tmp_path, config: Config):
    from loa_auction.ui import MiniWindow

    path = tmp_path / "config.json"
    save_config(config, path)
    win = MiniWindow(config, path, HistoryStore(tmp_path / "history.csv"))
    qtbot.addWidget(win)
    return win


def test_paste_only_mode_never_registers_hotkey_or_captures(qtbot, tmp_path, counters):
    win = make_window(qtbot, tmp_path, Config(capture_enabled=False))
    assert win.paste_only_check.isChecked()
    assert win._hotkey is None

    # 붙여넣기 경로는 그대로 동작한다
    QGuiApplication.clipboard().setImage(pil_to_qimage(Image.new("RGB", (640, 360), (20, 20, 20))))
    qtbot.keyClick(win, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(lambda: not win.busy, timeout=30000)
    # 단축키 콜백이 어떤 경로로든 불려도 캡처하지 않는다
    win._on_hotkey_activated()
    win.btn8.click()
    win.price_edit.setText("7776")
    qtbot.wait(300)

    assert counters["register"] == 0
    assert counters["grab"] == 0
    assert counters["mss"] == 0

    # 대조군: 모드를 끄면 등록 함수가 실제로 불린다(모킹이 제대로 연결됐음을 확인)
    win.paste_only_check.setChecked(False)
    assert counters["register"] == 1
    assert counters["grab"] == 0
    win.paste_only_check.setChecked(True)
    assert counters["unregister"] == 1
    win._on_hotkey_activated()
    assert counters["grab"] == 0


def test_capture_enabled_mode_registers_hotkey_but_does_not_capture_until_pressed(qtbot, tmp_path, counters):
    win = make_window(qtbot, tmp_path, Config(capture_enabled=True))
    assert counters["register"] == 1
    qtbot.wait(1500)  # 시간이 흘러도 저절로 캡처하지 않는다
    assert counters["grab"] == 0
    win._hotkey.activated.emit()  # 단축키 1회 = 캡처 1회
    assert counters["grab"] == 1
    qtbot.waitUntil(lambda: not win.busy, timeout=30000)
    qtbot.wait(500)
    assert counters["grab"] == 1


# ---------------------------------------------------------------------- (c)
LOOP_NODES = (ast.For, ast.AsyncFor, ast.While, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _ancestors(node, parents):
    while node in parents:
        node = parents[node]
        yield node


def _call_name(call: ast.Call) -> str:
    f = call.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def test_capture_function_called_only_from_hotkey_callback():
    sites = []
    for name, text in package_sources().items():
        tree = ast.parse(text)
        parents = _parents(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node) == "grab_primary_monitor":
                ancestors = list(_ancestors(node, parents))
                func = next(a for a in ancestors if isinstance(a, (ast.FunctionDef, ast.AsyncFunctionDef)))
                in_loop = any(isinstance(a, LOOP_NODES) for a in ancestors)
                sites.append((name, func.name, in_loop))
    assert sites == [("ui.py", "_on_hotkey_activated", False)]


def test_hotkey_callback_is_only_connected_to_hotkey_signal():
    tree = ast.parse(package_sources()["ui.py"])
    parents = _parents(tree)
    refs = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "_on_hotkey_activated"]
    assert refs, "단축키 콜백 참조가 없습니다"
    for ref in refs:
        call = parents[ref]
        assert isinstance(call, ast.Call) and _call_name(call) == "connect", ast.unparse(call)
        signal = call.func.value
        assert isinstance(signal, ast.Attribute) and signal.attr == "activated", ast.unparse(call)


def test_mss_used_only_inside_capture_function():
    for name, text in package_sources().items():
        tree = ast.parse(text)
        parents = _parents(tree)
        for node in ast.walk(tree):
            uses_mss = (isinstance(node, ast.Import) and any(a.name == "mss" for a in node.names)) or (
                isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "mss"
            )
            if uses_mss:
                func = next(
                    (a for a in _ancestors(node, parents) if isinstance(a, (ast.FunctionDef, ast.AsyncFunctionDef))),
                    None,
                )
                assert (name, func.name if func else None) == ("capture.py", "grab_primary_monitor")


def test_no_timers_in_application_code():
    for name, text in package_sources().items():
        for word in ("QTimer", "startTimer", "threading.Timer", "sched.scheduler", "QBasicTimer"):
            assert word not in text, f"{name}: {word}"
