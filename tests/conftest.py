import os
import re
import sys
from pathlib import Path

import pytest

# Qt를 import하기 전에 화면 없는 플랫폼을 지정한다.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES_DIR = ROOT / "fixtures"
FIXTURE_PATTERN = re.compile(r"^sample_.+_(\d+)\.(webp|png|jpg)$", re.IGNORECASE)

# 파일명만으로는 알 수 없는 아이템명 기대값. 목록에 없는 fixture는 "비어 있지 않음"만 확인한다.
EXPECTED_ITEM_SUBSTRING = {
    "sample_full_7776.webp": "전율",
    "sample_kakao_17500.png": "결투의 대가",
}


def fixture_images() -> list[Path]:
    if not FIXTURES_DIR.is_dir():
        return []
    return sorted(p for p in FIXTURES_DIR.iterdir() if FIXTURE_PATTERN.match(p.name))


def expected_price(path: Path) -> int:
    m = FIXTURE_PATTERN.match(path.name)
    assert m, path.name
    return int(m.group(1))


NO_FIXTURE_MESSAGE = (
    "fixtures/ 폴더에 sample_<이름>_<가격>.(webp|png|jpg) 이미지가 없습니다. "
    "fixtures/README.md를 참고해 경매 화면 캡처를 넣으세요."
)


def fixture_params() -> list:
    """fixture 이미지별 파라미터. 이미지가 없으면 실패용 자리표시자 하나(skip하지 않는다)."""
    images = fixture_images()
    if images:
        return [pytest.param(p, id=p.name) for p in images]
    return [pytest.param(None, id="no-fixture-images")]


def require_fixture(path: Path | None) -> Path:
    assert path is not None, NO_FIXTURE_MESSAGE
    return path


def pytest_collection_modifyitems(config, items):
    """skip/xfail 마커가 붙은 테스트가 있으면 수집 단계에서 실패시킨다."""
    for item in items:
        for name in ("skip", "skipif", "xfail"):
            if item.get_closest_marker(name) is not None:
                raise RuntimeError(f"{item.nodeid}: {name} 마커는 허용되지 않습니다")
