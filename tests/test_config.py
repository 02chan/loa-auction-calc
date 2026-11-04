import json

from loa_auction.config import Config, load_config, load_config_checked, save_config


def test_defaults_when_missing(tmp_path):
    cfg, warning = load_config_checked(tmp_path / "config.json")
    assert cfg == Config(hotkey="F8", last_party_size=4, capture_enabled=True, window_pos=None)
    assert warning is None


def test_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    save_config(Config(hotkey="Ctrl+Shift+A", last_party_size=8, capture_enabled=False, window_pos=[10, 20]), path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"hotkey": "Ctrl+Shift+A", "last_party_size": 8, "capture_enabled": False, "window_pos": [10, 20]}
    assert load_config(path) == Config("Ctrl+Shift+A", 8, False, [10, 20])


def test_invalid_values_fall_back_safely(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"hotkey": "", "last_party_size": 5, "window_pos": "x"}), "utf-8")
    assert load_config(path) == Config()
    # capture_enabled 값이 잘못됐으면 캡처를 끈다(안전한 쪽)
    path.write_text(json.dumps({"capture_enabled": "no"}), "utf-8")
    assert load_config(path).capture_enabled is False


def test_broken_json_is_backed_up_and_starts_paste_only(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"capture_enabled": false,}', "utf-8")
    cfg, warning = load_config_checked(path)
    assert cfg.capture_enabled is False
    assert warning and "config.json.bak" in warning
    assert (tmp_path / "config.json.bak").read_text("utf-8") == '{"capture_enabled": false,}'
