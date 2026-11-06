# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 설정. build.ps1이 이 파일로 dist\\LoaAuctionCalc.exe(onefile, 창 프로그램)를 만든다.

명령줄 옵션만으로는 PySide6·Pillow가 함께 끌고 오는, 이 앱이 쓰지 않는 파일을 뺄 수 없어서 여기서 걸러 낸다.
- 가상 키보드 입력 플러그인 → Qt Quick/QML (약 15MB)
- PDF 이미지 플러그인 → Qt Pdf
- 네트워크/TLS 플러그인 → 암호화 라이브러리
- 소프트웨어 OpenGL(opengl32sw.dll, 약 20MB)
- Pillow의 AVIF·글꼴 엔진
- Qt 번역 파일(약 7MB)
번들이 작아지면 exe가 실행될 때마다 임시 폴더에 풀어야 할 양이 줄어 시작이 빨라진다.
"""

import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH)  # noqa: F821 - PyInstaller가 spec을 실행할 때 넣어 주는 값
sys.path.insert(0, str(ROOT))
# 한국어가 아닌 코드 페이지의 콘솔/파이프에서도 아래 한국어 안내 출력 때문에 빌드가 멈추지 않게 한다.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")
from loa_auction.icon import write_ico  # noqa: E402

# exe 아이콘: 저장소에 이미지 파일을 두지 않고 코드(loa_auction/icon.py)로 그려 만든다.
ICON = write_ico(ROOT / "build" / "icon" / "LoaAuctionCalc.ico")

# fixtures/sample_<이름>_<가격>.(webp|png|jpg)가 있으면 --selftest용으로 함께 넣는다(없어도 빌드는 성공).
FIXTURE_RE = re.compile(r"^sample_.+_\d+\.(webp|png|jpg)$", re.IGNORECASE)
FIXTURE_DIR = ROOT / "fixtures"
fixtures = (
    sorted(p for p in FIXTURE_DIR.iterdir() if p.is_file() and FIXTURE_RE.match(p.name)) if FIXTURE_DIR.is_dir() else []
)
for p in fixtures:
    print(f"  fixture 포함: {p.name}")
if not fixtures:
    print("  fixtures 이미지 없음: OCR 샘플 없이 빌드합니다 (--selftest는 계산만 검사).")

# 앱이 쓰지 않는 파이썬 모듈. 모두 없어도 import 오류가 나지 않는 것만 넣었다.
EXCLUDED_MODULES = [
    "PySide6.QtNetwork",  # 네트워크 미사용. PySide6는 try/except로만 불러온다 → TLS 플러그인·암호화 DLL도 빠짐
    "PIL.AvifImagePlugin",  # AVIF 미사용. Pillow는 없는 형식 플러그인을 건너뛴다
    "PIL._avif",
    "PIL._imagingft",  # 글꼴 렌더링 미사용. ImageFont가 지연 오류로 처리한다
    "PIL.ImageQt",
    "PIL.ImageTk",
    "PIL._imagingtk",
    "tkinter",
    "ssl",  # TLS 미사용(asyncio 등은 없으면 건너뛴다)
    "_ssl",
    "_hashlib",  # hashlib는 OpenSSL이 없으면 내장 구현을 쓴다
]

# 앱이 쓰지 않는 Qt 바이너리·플러그인(번들 안 경로 기준). 남는 파일이 이것들에 의존하지 않음을 확인했다.
UNUSED_BINARY = re.compile(
    r"""^PySide6[\\/](
        opengl32sw\.dll                                          # 소프트웨어 OpenGL: 위젯 앱은 래스터로 그린다
      | Qt6(Quick|Qml\w*|Pdf|VirtualKeyboard|OpenGL|Network|Svg)\.dll
      | plugins[\\/](platforminputcontexts|tls|networkinformation|generic|iconengines)[\\/]
      | plugins[\\/]platforms[\\/](?!qwindows\.dll$)             # 창 플랫폼은 qwindows만 쓴다
      | plugins[\\/]imageformats[\\/](?!q(jpeg|webp|ico|gif)\.dll$)
    )""",
    re.IGNORECASE | re.VERBOSE,
)

a = Analysis(
    [str(ROOT / "loa_auction" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(p), "fixtures") for p in fixtures],
    hiddenimports=collect_submodules("winrt"),
    excludes=EXCLUDED_MODULES,
    noarchive=False,
    optimize=0,
)
# Qt 번역 파일(약 7MB): 앱이 번역기(QTranslator)를 쓰지 않으므로 필요 없다.
UNUSED_DATA = re.compile(r"^PySide6[\\/]translations[\\/]", re.IGNORECASE)

removed = [entry[0] for entry in a.binaries if UNUSED_BINARY.search(entry[0])]
a.binaries = [entry for entry in a.binaries if not UNUSED_BINARY.search(entry[0])]
removed_data = [entry[0] for entry in a.datas if UNUSED_DATA.search(entry[0])]
a.datas = [entry for entry in a.datas if not UNUSED_DATA.search(entry[0])]
print(f"  쓰지 않는 Qt 파일 {len(removed)}개, 번역 파일 {len(removed_data)}개 제외")

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="LoaAuctionCalc",
    debug=False,
    strip=False,
    upx=False,  # UPX 압축은 백신 오탐을 부를 수 있어 쓰지 않는다
    runtime_tmpdir=None,
    console=False,  # 창 프로그램(--windowed)
    icon=[str(ICON)],
)
