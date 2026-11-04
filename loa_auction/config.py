"""config.json 읽기/쓰기와 실행 폴더 경로."""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_FILENAME = "config.json"
DEFAULT_HOTKEY = "F8"
DEFAULT_PARTY_SIZE = 4


def app_dir() -> Path:
    """exe와 같은 폴더. 개발 중(소스 실행)에는 프로젝트 루트."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundle_dir() -> Path:
    """PyInstaller onefile이 풀린 임시 폴더(번들 리소스 위치). 소스 실행이면 프로젝트 루트."""
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else app_dir()


def default_config_path() -> Path:
    return app_dir() / CONFIG_FILENAME


def _point_or_none(value: object) -> list[int] | None:
    """[x, y] 정수 두 개면 그대로, 아니면 None."""
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(v, int) and not isinstance(v, bool) for v in value)
    ):
        return [value[0], value[1]]
    return None


@dataclass
class Config:
    hotkey: str = DEFAULT_HOTKEY
    last_party_size: int = DEFAULT_PARTY_SIZE
    capture_enabled: bool = True
    window_pos: list[int] | None = field(default=None)
    # 사용자가 아이콘(접힌 상태)을 끌어다 놓은 위치. 없으면 창 왼쪽 위에 접힌다.
    icon_pos: list[int] | None = field(default=None)

    @classmethod
    def from_dict(cls, data: object) -> "Config":
        cfg = cls()
        if not isinstance(data, dict):
            return cfg
        hotkey = data.get("hotkey")
        if isinstance(hotkey, str) and hotkey.strip():
            cfg.hotkey = hotkey.strip()
        party = data.get("last_party_size")
        if party in (4, 8) and not isinstance(party, bool):
            cfg.last_party_size = party
        if "capture_enabled" in data:
            capture = data["capture_enabled"]
            # 값이 잘못됐으면 안전한 쪽(붙여넣기 전용 모드)으로 둔다.
            cfg.capture_enabled = capture if isinstance(capture, bool) else False
        cfg.window_pos = _point_or_none(data.get("window_pos"))
        cfg.icon_pos = _point_or_none(data.get("icon_pos"))
        return cfg

    def to_dict(self) -> dict:
        return asdict(self)


def load_config_checked(path: Path | None = None) -> tuple[Config, str | None]:
    """(설정, 경고 문구). 파일이 없으면 기본값.

    파일이 있는데 읽을 수 없으면(JSON 문법 오류 등) config.json.bak으로 백업하고,
    사용자가 붙여넣기 전용 모드를 켜 두었을 수 있으므로 캡처를 끈 상태로 시작한다.
    """
    path = Path(path) if path else default_config_path()
    if not path.exists():
        return Config(), None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        backup = path.with_name(path.name + ".bak")
        try:
            shutil.copyfile(path, backup)
        except OSError:
            pass
        return (
            Config(capture_enabled=False),
            f"{path.name}을 읽지 못해 {backup.name}로 백업하고 붙여넣기 전용 모드로 시작했습니다.",
        )
    return Config.from_dict(data), None


def load_config(path: Path | None = None) -> Config:
    return load_config_checked(path)[0]


def save_config(config: Config, path: Path | None = None) -> None:
    path = Path(path) if path else default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(config.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
