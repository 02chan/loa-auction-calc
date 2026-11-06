"""화면 캡처(mss, 그 순간 1회)와 QImage ↔ PIL 변환.

화면 캡처는 사용자가 단축키(F8)나 [캡처] 버튼을 누른 순간에만 호출된다
(ui.MiniWindow.capture_and_recognize). 붙여넣기 전용 모드에서는 grab_screens()가 호출되지 않는다.
"""

from __future__ import annotations

from PIL import Image
from PySide6.QtGui import QImage


def grab_screens() -> list[Image.Image]:
    """모든 모니터를 그 순간 한 번씩 캡처해 RGB PIL 이미지 목록으로 돌려준다(주 모니터가 먼저).

    게임이 주 모니터가 아닌 곳에 떠 있어도 인식할 수 있도록 모니터마다 따로 담는다.
    """
    import mss  # 붙여넣기 전용 모드에서는 불러오지도 않는다

    open_screen = getattr(mss, "MSS", None) or mss.mss  # mss 10은 MSS, 이전 버전은 mss()
    with open_screen() as sct:
        monitors = sct.monitors[1:]  # [0]은 모든 모니터를 합친 가상 화면
        monitors.sort(key=lambda m: 0 if m.get("is_primary") or (m["left"] == 0 and m["top"] == 0) else 1)
        shots = [sct.grab(m) for m in monitors]
    return [Image.frombytes("RGB", s.size, s.bgra, "raw", "BGRX") for s in shots]


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
