"""화면 캡처(mss, 주 모니터 1회)와 QImage ↔ PIL 변환.

화면 캡처는 사용자가 단축키를 누른 순간에만 호출된다(ui.MiniWindow._on_hotkey_activated).
붙여넣기 전용 모드에서는 이 모듈의 grab_primary_monitor()가 호출되지 않는다.
"""

from __future__ import annotations

from PIL import Image
from PySide6.QtGui import QImage


def grab_primary_monitor() -> Image.Image:
    """주 모니터 전체를 한 번 캡처해 RGB PIL 이미지로 돌려준다."""
    import mss  # 붙여넣기 전용 모드에서는 불러오지도 않는다

    with mss.mss() as sct:
        monitors = sct.monitors[1:]  # [0]은 모든 모니터를 합친 가상 화면
        primary = next((m for m in monitors if m["left"] == 0 and m["top"] == 0), monitors[0])
        shot = sct.grab(primary)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def qimage_to_pil(qimg: QImage) -> Image.Image:
    rgba = qimg.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h, bpl = rgba.width(), rgba.height(), rgba.bytesPerLine()
    data = bytes(rgba.constBits())
    return Image.frombuffer("RGBA", (w, h), data, "raw", "RGBA", bpl, 1).convert("RGB")


def pil_to_qimage(img: Image.Image) -> QImage:
    rgba = img.convert("RGBA")
    data = rgba.tobytes("raw", "RGBA")
    qimg = QImage(data, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888)
    return qimg.copy()  # data 버퍼 수명과 분리
