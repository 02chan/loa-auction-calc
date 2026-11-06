"""항상 위에 떠 있는 미니 창.

입력 경로는 두 가지뿐이다.
- Ctrl+V: 클립보드 이미지 → OCR → 계산
- 전역 단축키(기본 F8): 주 모니터를 그 순간 1회 캡처 → OCR → 계산
  (config.capture_enabled=false 인 "붙여넣기 전용 모드"에서는 단축키 등록·캡처를 하지 않는다)

게임 창에는 아무것도 보내지 않는다. 이 창은 숫자를 보여 주기만 하고 입찰은 사람이 한다.
"""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import Callable

from PIL import Image
from PySide6.QtCore import QMetaObject, QObject, QPoint, QRectF, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QIcon,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QRegularExpressionValidator,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import capture, ocr
from . import hotkey as hotkey_mod
from .calc import CalcResult, calculate
from .config import Config, save_config
from .history import HistoryStore
from .icon import render_icon
from .ocr import OcrResult, OcrUnavailableError

GOLD = "#F5C242"  # 골드 아이콘 색
WINDOW_BG = "#1d1f26"
WINDOW_BORDER = "#454a58"
WINDOW_RADIUS = 8
RECENT_COUNT = 10
FAIL_TEXT = "인식 실패"
NO_IMAGE_TEXT = "클립보드에 이미지가 없습니다. Win+Shift+S로 경매 창을 캡처한 뒤 Ctrl+V 하세요."

STYLE = f"""
QWidget {{ color: #e6e6e6; font-family: 'Malgun Gothic'; font-size: 9pt; }}
QLineEdit {{ background: #2a2d36; border: 1px solid #3c404c; border-radius: 4px; padding: 3px 6px; font-size: 11pt; }}
QLineEdit:focus {{ border-color: {GOLD}; }}
QPushButton {{ background: #2a2d36; border: 1px solid #3c404c; border-radius: 4px; padding: 4px 0; }}
QPushButton:checked {{ background: {GOLD}; color: #1d1f26; font-weight: bold; border-color: {GOLD}; }}
QLabel#ItemName {{ font-weight: bold; font-size: 10pt; }}
QLabel#Bid {{ color: {GOLD}; font-size: 26pt; font-weight: bold; }}
QLabel#Caption, QLabel#Small {{ color: #a8acb8; }}
QLabel#Small:hover {{ color: #ffffff; text-decoration: underline; }}
QLabel#Bid:hover {{ color: #ffd86b; }}
QLabel#Status {{ color: #9aa0ad; font-size: 8pt; }}
QToolButton {{ border: none; color: #a8acb8; }}
QToolButton#CollapseButton, QToolButton#CloseButton {{
    border: none; border-radius: 4px; background: transparent; color: #c9ccd6; font-size: 10pt; }}
QToolButton#CollapseButton:hover {{ background: #343844; color: #ffffff; }}
QToolButton#CaptureButton {{ border: 1px solid #3c404c; border-radius: 4px; background: #2a2d36;
    color: {GOLD}; font-size: 8pt; padding: 0 6px; }}
QToolButton#CaptureButton:hover {{ background: #343844; border-color: {GOLD}; }}
QToolButton#CloseButton:hover {{ background: #c42b1c; color: #ffffff; }}
QMenu {{ background: #23262e; border: 1px solid {WINDOW_BORDER}; padding: 4px; }}
QMenu::item {{ color: #e6e6e6; padding: 5px 22px 5px 14px; border-radius: 3px; }}
QMenu::item:selected {{ background: #343844; color: #ffffff; }}
QMenu::separator {{ height: 1px; background: #3c404c; margin: 3px 6px; }}
QTableWidget {{ background: #23262e; gridline-color: #333743; font-size: 8pt; }}
QHeaderView::section {{ background: #2a2d36; color: #a8acb8; border: none; padding: 2px; font-size: 8pt; }}
QCheckBox {{ color: #a8acb8; font-size: 8pt; }}
"""


def _fmt(value: int) -> str:
    return f"{value:,}"


WINDOW_ICON_SIZES = (16, 24, 32, 48, 64)


@lru_cache(maxsize=1)
def app_icon() -> QIcon:
    """제목 표시줄·작업 표시줄용 아이콘(코드로 그린 골드 코인 + 경매 망치)."""
    icon = QIcon()
    for n in WINDOW_ICON_SIZES:
        icon.addPixmap(QPixmap.fromImage(capture.pil_to_qimage(render_icon(n))))
    return icon


class _OcrSignals(QObject):
    finished = Signal(object)  # OcrResult
    unavailable = Signal(str)
    failed = Signal(str)


class OcrTask(QRunnable):
    """OCR을 스레드풀에서 실행한다(UI 스레드를 멈추지 않도록)."""

    def __init__(self, images: Image.Image | list[Image.Image], ocr_func: Callable[[Image.Image], OcrResult]):
        super().__init__()
        self.images = images if isinstance(images, list) else [images]
        self.ocr_func = ocr_func
        self.signals = _OcrSignals()

    def _recognize_first(self) -> OcrResult:
        """이미지(모니터)를 차례로 읽어 처음 성공한 결과. 모두 실패하면 마지막 실패 결과."""
        result = OcrResult.failed()
        for image in self.images:
            result = self.ocr_func(image)
            if result.success:
                break
        return result

    def run(self) -> None:
        try:
            result = self._recognize_first()
        except OcrUnavailableError as exc:
            self.signals.unavailable.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - UI에 오류를 표시한다
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.signals.finished.emit(result)


class PriceEdit(QLineEdit):
    """숫자만 받는 가격 칸.

    Ctrl+V: 클립보드가 숫자 글자면 가격으로 붙여 넣고, 그 밖(이미지·다른 글자·빈 클립보드)은
    창으로 넘겨 이미지 인식 또는 안내 문구를 처리하게 한다.
    """

    pasteRequested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        # 이 창이 방금 클립보드에 복사한 숫자. 이 값은 가격 칸에 다시 붙이지 않는다
        # (새 캡처 없이 Ctrl+V를 눌렀을 때 입찰가가 가격으로 들어가는 실수 방지).
        self.own_copied_text: str | None = None

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.matches(QKeySequence.StandardKey.Paste):
            digits = _clipboard_digits()
            if digits is not None and digits != self.own_copied_text and not _clipboard_has_image():
                self.insert(digits)
            else:
                self.pasteRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class CopyLabel(QLabel):
    """클릭하면 clicked 시그널을 내는 숫자 라벨(값 복사용)."""

    clicked = Signal()

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("클릭하면 숫자를 쉼표 없이 복사합니다")
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setProperty("copyable", True)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class DragArea(QWidget):
    """누르고 끌면 창 전체가 움직이는 영역(윈도우 제목 표시줄 대신)."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._offset: QPoint | None = None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()  # Windows가 직접 옮긴다(화면 가장자리 맞춤 등 동작 유지)
                return
            self._offset = event.globalPosition().toPoint() - self.window().pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._offset = None
        super().mouseReleaseEvent(event)


class IconBubble(QWidget):
    """창을 접었을 때 화면에 떠 있는 작은 프로그램 아이콘.

    클릭: 원래 창으로 펼치기 / 끌기: 옮기기 / 오른쪽 클릭: 펼치기·종료 메뉴
    작업 표시줄에는 나오지 않는 도구 창이다.
    """

    SIZE = 48
    clicked = Signal()
    exitRequested = Signal()
    movedByUser = Signal(QPoint)  # 사용자가 끌어서 옮긴 새 위치(프로그램이 place()로 놓은 것은 제외)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(
            parent,
            Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint,
        )
        self._placing = False
        self._placed_at: QPoint | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("클릭: 펼치기 · 끌기: 옮기기 · 오른쪽 클릭: 메뉴")
        self.setWindowTitle("로아 경매 계산기")
        pixmap = QPixmap.fromImage(capture.pil_to_qimage(render_icon(self.SIZE * 2)))
        pixmap.setDevicePixelRatio(2.0)  # 고해상도 화면에서도 선명하게
        self._pixmap = pixmap
        self._press: QPoint | None = None
        self._dragging = False
        self._offset: QPoint | None = None
        self.hovered = False

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.drawPixmap(self.rect(), self._pixmap)
        if self.hovered:  # 마우스를 올리면 골드 테두리
            p.setPen(QPen(QColor(GOLD), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            radius = self.SIZE * 0.2
            p.drawRoundedRect(QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5), radius, radius)
        p.end()

    def enterEvent(self, event) -> None:  # noqa: N802
        self.hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.globalPosition().toPoint()
            self._dragging = False
            self._offset = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._press is not None and event.buttons() & Qt.MouseButton.LeftButton:
            point = event.globalPosition().toPoint()
            if not self._dragging and (point - self._press).manhattanLength() >= QApplication.startDragDistance():
                self._dragging = True  # 일정 거리 이상 움직이면 클릭이 아니라 끌기
                handle = self.windowHandle()
                if handle is None or not handle.startSystemMove():
                    self._offset = self._press - self.pos()
            if self._offset is not None:
                self.move(point - self._offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._press is not None and not self._dragging:
            self.clicked.emit()
        self._press, self._dragging, self._offset = None, False, None
        super().mouseReleaseEvent(event)

    def place(self, point: QPoint) -> None:
        """프로그램이 아이콘을 놓는다(사용자가 옮긴 위치로 기억하지 않는다)."""
        self._placed_at = QPoint(point)
        self._placing = True
        try:
            self.move(point)
        finally:
            self._placing = False

    def moveEvent(self, event) -> None:  # noqa: N802
        # Windows 시스템 끌기(startSystemMove) 중에는 마우스 이벤트가 오지 않으므로 이동 이벤트로 알아낸다.
        # place()로 놓은 자리에서 생기는 이동 이벤트(표시될 때의 늦은 이벤트 포함)는 사용자 이동이 아니다.
        if not self._placing and self.pos() != self._placed_at:
            self.movedByUser.emit(self.pos())
        super().moveEvent(event)

    def build_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction("펼치기", self.clicked.emit)
        menu.addSeparator()
        menu.addAction("종료", self.exitRequested.emit)
        return menu

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        self.build_menu().exec(event.globalPos())


class _ActualPriceDelegate(QStyledItemDelegate):
    """실제낙찰가 편집기. 편집을 시작한 기록 행 번호를 기억해, 편집 중 표가 다시 그려져도
    엉뚱한 행에 저장되지 않게 한다."""

    def __init__(self, on_commit: Callable[[int, str], None], parent=None):
        super().__init__(parent)
        self._on_commit = on_commit

    def createEditor(self, parent, option, index):  # noqa: N802
        editor = QLineEdit(parent)
        editor.setValidator(QRegularExpressionValidator(r"[\d,]{0,13}", editor))
        editor.setProperty("history_index", index.data(Qt.ItemDataRole.UserRole))
        return editor

    def setEditorData(self, editor, index):  # noqa: N802
        if not editor.property("initialized"):
            editor.setText(index.data(Qt.ItemDataRole.DisplayRole) or "")
            editor.setProperty("initialized", True)

    def setModelData(self, editor, model, index):  # noqa: N802
        history_index = editor.property("history_index")
        if history_index is not None:
            self._on_commit(int(history_index), editor.text())


def _clipboard_has_image() -> bool:
    mime = QGuiApplication.clipboard().mimeData()
    return mime is not None and mime.hasImage()


def _clipboard_digits() -> str | None:
    text = QGuiApplication.clipboard().text().replace(",", "").strip()
    return text if text.isdigit() else None


class MiniWindow(QWidget):
    COL_TIME, COL_ITEM, COL_PRICE, COL_PARTY, COL_BID, COL_ACTUAL = range(6)

    exitRequested = Signal()  # ✕ 또는 아이콘 메뉴의 '종료' (앱 종료 요청)

    def __init__(
        self,
        config: Config,
        config_path: Path,
        history: HistoryStore,
        ocr_func: Callable[[Image.Image], OcrResult] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.config = config
        self.config_path = Path(config_path)
        self.history = history
        self._ocr_func = ocr_func or ocr.recognize
        self._hotkey: hotkey_mod.GlobalHotkey | None = None
        self._busy = False
        self._task: OcrTask | None = None
        self._filling_table = False
        self._history_dirty = False
        self.result: CalcResult | None = None  # 현재 표시 중인 계산 결과
        self._price_edited = False
        self._collapsed = False

        self.setObjectName("MiniWindow")
        self.setWindowTitle("로아 경매 계산기")
        self.setWindowIcon(app_icon())
        # 윈도우 제목 표시줄 없이 항상 위. 배경(둥근 사각형)은 paintEvent에서 직접 그린다.
        self.setWindowFlags(
            Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(STYLE)
        self._build_ui()
        self.bubble = IconBubble(self)
        self.bubble.clicked.connect(self.expand)
        self.bubble.exitRequested.connect(self.exit_app)
        self.bubble.movedByUser.connect(self._on_icon_moved_by_user)
        self.resize(320, 240)
        self._restore_position()

        self.set_party_size(self.config.last_party_size, save=False)
        self.paste_only_check.setChecked(not self.config.capture_enabled)
        self.paste_only_check.toggled.connect(self._on_paste_only_toggled)
        self.refresh_history()
        self._setup_hotkey()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 6)
        root.setSpacing(5)

        # 맨 윗줄: [아이콘] 아이템명 ........ [—] [✕]  (이 줄을 끌어서 창을 옮긴다)
        self.header = DragArea()
        self.header.setToolTip("끌어서 창 옮기기")
        header_row = QHBoxLayout(self.header)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(6)
        icon_label = QLabel()
        icon_label.setPixmap(app_icon().pixmap(16, 16))
        icon_label.setFixedSize(16, 16)
        self.item_label = QLabel("경매 화면을 캡처하세요")
        self.item_label.setObjectName("ItemName")
        # 글자 선택을 끄면 이 줄 어디를 눌러도 창 끌기로 동작한다
        self.item_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.collapse_button = QToolButton()
        self.collapse_button.setObjectName("CollapseButton")
        self.collapse_button.setText("—")
        self.collapse_button.setToolTip("아이콘으로 접기")
        self.collapse_button.setFixedSize(24, 22)
        self.collapse_button.clicked.connect(self.collapse)
        self.close_button = QToolButton()
        self.close_button.setObjectName("CloseButton")
        self.close_button.setText("✕")
        self.close_button.setToolTip("종료")
        self.close_button.setFixedSize(24, 22)
        self.close_button.clicked.connect(self.exit_app)
        # 게임이 앞에 있으면 F8이 계산기로 전달되지 않을 수 있어, 마우스로 누르는 캡처 버튼을 둔다.
        self.capture_button = QToolButton()
        self.capture_button.setObjectName("CaptureButton")
        self.capture_button.setText("캡처")
        self.capture_button.setToolTip("지금 화면(모든 모니터)을 한 번 캡처해 인식합니다. 게임에서 F8이 안 먹을 때 쓰세요.")
        self.capture_button.setFixedHeight(22)
        self.capture_button.clicked.connect(self._on_capture_clicked)
        for button in (self.capture_button, self.collapse_button, self.close_button):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        header_row.addWidget(icon_label)
        header_row.addWidget(self.item_label, 1)
        header_row.addWidget(self.capture_button)
        header_row.addWidget(self.collapse_button)
        header_row.addWidget(self.close_button)
        root.addWidget(self.header)

        row = QHBoxLayout()
        caption = QLabel("거래소 가격")
        caption.setObjectName("Caption")
        self.price_edit = PriceEdit()
        self.price_edit.setPlaceholderText("숫자 입력")
        self.price_edit.setValidator(QRegularExpressionValidator(r"\d{0,10}", self.price_edit))
        self.price_edit.textChanged.connect(self.recalculate)
        self.price_edit.textEdited.connect(self._on_price_edited)  # 사용자가 직접 고칠 때만
        self.price_edit.editingFinished.connect(self._on_price_edit_finished)
        self.price_edit.pasteRequested.connect(self.paste_from_clipboard)
        self.btn4 = QPushButton("4인")
        self.btn8 = QPushButton("8인")
        self.party_group = QButtonGroup(self)
        self.party_group.setExclusive(True)
        for size, btn in ((4, self.btn4), (8, self.btn8)):
            btn.setCheckable(True)
            btn.setFixedWidth(44)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.party_group.addButton(btn, size)
        self.party_group.idClicked.connect(self._on_party_clicked)
        row.addWidget(caption)
        row.addWidget(self.price_edit, 1)
        row.addWidget(self.btn4)
        row.addWidget(self.btn8)
        root.addLayout(row)

        bid_caption = QLabel("추천 입찰가")
        bid_caption.setObjectName("Caption")
        self.bid_label = CopyLabel("-")
        self.bid_label.setObjectName("Bid")
        self.bid_label.clicked.connect(self.copy_recommended_bid)
        self.bid_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bid_font = QFont(self.bid_label.font())
        bid_font.setPointSize(26)
        bid_font.setBold(True)
        self.bid_label.setFont(bid_font)
        bid_row = QHBoxLayout()
        bid_row.addWidget(bid_caption, 0, Qt.AlignmentFlag.AlignBottom)
        bid_row.addWidget(self.bid_label, 1)
        root.addLayout(bid_row)

        small = QHBoxLayout()
        self.sell_label = CopyLabel("판매 손익분기 -")
        self.use_label = CopyLabel("사용 손익분기 -")
        for lbl in (self.sell_label, self.use_label):
            lbl.setObjectName("Small")
        self.sell_label.clicked.connect(self.copy_sell_breakeven)
        self.use_label.clicked.connect(self.copy_use_breakeven)
        small.addWidget(self.sell_label)
        small.addStretch(1)
        small.addWidget(self.use_label)
        root.addLayout(small)

        opts = QHBoxLayout()
        self.history_toggle = QToolButton()
        self.history_toggle.setCheckable(True)
        self.history_toggle.setText("▸ 최근 기록")
        self.history_toggle.toggled.connect(self._on_history_toggled)
        self.paste_only_check = QCheckBox("붙여넣기 전용 모드")
        self.paste_only_check.setToolTip("켜면 단축키 등록과 화면 캡처를 하지 않고 Ctrl+V만 받습니다.")
        opts.addWidget(self.history_toggle)
        opts.addStretch(1)
        opts.addWidget(self.paste_only_check)
        root.addLayout(opts)

        self.history_table = QTableWidget(0, 6)
        self.history_table.setHorizontalHeaderLabels(["시각", "아이템", "최저가", "인원", "추천가", "실제낙찰가"])
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.history_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        self._actual_delegate = _ActualPriceDelegate(self._commit_actual_price, self.history_table)
        self.history_table.setItemDelegateForColumn(self.COL_ACTUAL, self._actual_delegate)
        # 편집기가 닫힌 뒤(이벤트 루프 다음 차례) 미뤄 둔 표 갱신을 한다.
        self._actual_delegate.closeEditor.connect(self._on_editor_closed, Qt.ConnectionType.QueuedConnection)
        header = self.history_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(self.COL_ITEM, QHeaderView.ResizeMode.Stretch)
        self.history_table.itemChanged.connect(self._on_history_item_changed)
        self.history_table.setVisible(False)
        self.history_table.setMinimumHeight(180)
        root.addWidget(self.history_table, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("Status")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

    # ------------------------------------------------------------ 계산
    @property
    def party_size(self) -> int:
        return self.config.last_party_size

    @Slot(int)
    def set_party_size(self, size: int, save: bool = True) -> None:
        if size not in (4, 8):
            return
        self.config.last_party_size = size
        button = self.party_group.button(size)
        if button is not None and not button.isChecked():
            button.setChecked(True)
        if save:
            self.save_config()
        self.recalculate()

    def current_price(self) -> int | None:
        text = self.price_edit.text().replace(",", "").strip()
        return int(text) if text.isdigit() else None

    @Slot()
    def recalculate(self) -> None:
        price = self.current_price()
        if price is None:
            self.result = None
            self.bid_label.setText("-")
            self.sell_label.setText("판매 손익분기 -")
            self.use_label.setText("사용 손익분기 -")
            return
        self.result = calculate(price, self.party_size)
        self.bid_label.setText(_fmt(self.result.recommended_bid))
        self.sell_label.setText(f"판매 손익분기 {_fmt(self.result.sell_breakeven)}")
        self.use_label.setText(f"사용 손익분기 {_fmt(self.result.use_breakeven)}")

    @Slot(int)
    def _on_party_clicked(self, size: int) -> None:
        self.set_party_size(size)
        self.copy_recommended_bid()

    @Slot()
    def _on_price_edited(self) -> None:
        self._price_edited = True

    @Slot()
    def _on_price_edit_finished(self) -> None:
        """가격을 직접 고치고 Enter를 누르거나 칸을 벗어나면 추천 입찰가를 복사한다.

        - 타자마다 복사하면 Windows 클립보드 기록이 중간 숫자로 가득 차므로 끝났을 때만 복사한다.
        - 직접 고친 경우에만 복사한다. 게임으로 창을 옮겨 칸이 포커스를 잃는 것만으로는
          복사하지 않는다(방금 클릭해서 복사한 손익분기 값을 덮어쓰지 않도록).
        """
        if self._price_edited:
            self._price_edited = False
            self.copy_recommended_bid()

    # ------------------------------------------------------------ 클립보드 복사
    def copy_number(self, value: int, what: str) -> str:
        """value를 쉼표 없는 숫자 글자로 클립보드에 넣는다."""
        text = str(value)
        QGuiApplication.clipboard().setText(text)
        self.price_edit.own_copied_text = text
        self.set_status(f"{what} {text} 복사됨")
        return text

    @Slot()
    def copy_recommended_bid(self) -> str | None:
        if self.result is None:
            return None
        return self.copy_number(self.result.recommended_bid, "추천 입찰가")

    @Slot()
    def copy_sell_breakeven(self) -> str | None:
        if self.result is None:
            return None
        return self.copy_number(self.result.sell_breakeven, "판매 손익분기")

    @Slot()
    def copy_use_breakeven(self) -> str | None:
        if self.result is None:
            return None
        return self.copy_number(self.result.use_breakeven, "사용 손익분기")

    # ------------------------------------------------------------ 입력: Ctrl+V
    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.matches(QKeySequence.StandardKey.Paste):
            self.paste_from_clipboard()
            event.accept()
            return
        super().keyPressEvent(event)

    @Slot()
    def paste_from_clipboard(self) -> bool:
        clipboard = QGuiApplication.clipboard()
        mime = clipboard.mimeData()
        if mime is None or not mime.hasImage():
            self.set_status(NO_IMAGE_TEXT)
            return False
        qimg = clipboard.image()
        if qimg.isNull():
            self.set_status(NO_IMAGE_TEXT)
            return False
        return self.start_ocr(capture.qimage_to_pil(qimg), "붙여넣기")

    # ------------------------------------------------------------ 입력: 전역 단축키
    def _setup_hotkey(self) -> None:
        self.capture_button.setVisible(self.config.capture_enabled)  # 붙여넣기 전용 모드에서는 숨김
        if not self.config.capture_enabled:
            self.set_status("붙여넣기 전용 모드: Win+Shift+S로 캡처한 뒤 이 창에서 Ctrl+V 하세요.")
            return
        if self._hotkey is None:
            self._hotkey = hotkey_mod.GlobalHotkey(self)
            self._hotkey.activated.connect(self._on_hotkey_activated)
        key = self.config.hotkey
        try:
            ok = self._hotkey.register(key)
        except ValueError as exc:
            self.set_status(f"단축키 설정 오류({key}): {exc}. config.json의 hotkey 값을 확인하세요.")
            return
        if ok:
            self.set_status(f"{key} 또는 [캡처]: 화면 캡처 후 인식 / Ctrl+V: 클립보드 이미지 인식")
        else:
            self.set_status(
                f"단축키 {key} 등록 실패(다른 프로그램이 사용 중일 수 있음). "
                "config.json의 hotkey를 바꾸거나 Ctrl+V를 쓰세요."
            )

    def _teardown_hotkey(self) -> None:
        if self._hotkey is not None:
            self._hotkey.unregister()

    @Slot()
    def _on_hotkey_activated(self) -> None:
        """단축키(F8)를 누른 순간."""
        self.capture_and_recognize("F8")

    @Slot()
    def _on_capture_clicked(self) -> None:
        """[캡처] 버튼을 누른 순간. 게임이 앞에 있을 때 F8이 전달되지 않는 경우에 쓴다."""
        self.capture_and_recognize("캡처 버튼")

    def capture_and_recognize(self, source: str) -> None:
        """사용자가 누른 순간에만 모든 모니터를 1회 캡처해 인식한다."""
        if not self.config.capture_enabled:
            return
        if self._busy:
            self.set_status("이전 인식이 끝나지 않았습니다. 잠시 후 다시 누르세요.")
            return
        try:
            images = capture.grab_screens()
        except Exception as exc:  # noqa: BLE001
            self.set_status(f"화면 캡처 실패: {exc}")
            return
        self.start_ocr(images, source)

    @Slot(bool)
    def _on_paste_only_toggled(self, paste_only: bool) -> None:
        self.config.capture_enabled = not paste_only
        self.save_config()
        if paste_only:
            self._teardown_hotkey()
        self._setup_hotkey()

    # ------------------------------------------------------------ OCR
    def start_ocr(self, image: Image.Image | list[Image.Image], source: str) -> bool:
        if self._busy:
            self.set_status("이전 인식이 끝나지 않았습니다.")
            return False
        self._busy = True
        self.set_status(f"인식 중… ({source})")
        task = OcrTask(image, self._ocr_func)
        task.signals.finished.connect(self._on_ocr_finished)
        task.signals.unavailable.connect(self._on_ocr_unavailable)
        task.signals.failed.connect(self._on_ocr_error)
        self._task = task  # 시그널 객체가 끝까지 살아 있도록 참조 유지
        QThreadPool.globalInstance().start(task)
        return True

    @property
    def busy(self) -> bool:
        return self._busy

    @Slot(object)
    def _on_ocr_finished(self, result: OcrResult) -> None:
        self._busy = False
        self.expand()  # 아이콘으로 접혀 있었다면 결과를 볼 수 있게 펼친다
        if not result.success or result.price is None:
            self.show_failure("가격을 찾지 못했습니다. 경매 창의 '거래소 최저가'가 보이게 캡처하세요.")
            return
        name = result.item_name or "(아이템명 미인식)"
        self.item_label.setText(name)
        self.item_label.setToolTip(name)
        self.price_edit.setText(str(result.price))  # textChanged → 즉시 재계산
        copied = self.copy_recommended_bid()
        done = f"인식 완료: {_fmt(result.price)} 골드 ({self.party_size}인) · 추천 입찰가 {copied} 복사됨"
        self.set_status(done)
        bid = calculate(result.price, self.party_size).recommended_bid
        try:
            added = self.history.append(result.item_name, result.price, self.party_size, bid)
        except OSError as exc:
            self.set_status(
                f"{done}. 기록 저장 실패 "
                f"(history.csv가 다른 프로그램에서 열려 있는지 확인하세요: {exc.strerror or exc})"
            )
            return
        if added:
            self.refresh_history()

    @Slot(str)
    def _on_ocr_unavailable(self, message: str) -> None:
        self._busy = False
        self.expand()
        self.show_failure("한국어 OCR을 사용할 수 없습니다.")
        self.show_ocr_unavailable(message)

    @Slot(str)
    def _on_ocr_error(self, message: str) -> None:
        self._busy = False
        self.expand()
        self.show_failure(f"OCR 오류: {message}")

    def show_failure(self, detail: str) -> None:
        self.item_label.setText(FAIL_TEXT)
        self.item_label.setToolTip("")
        self.price_edit.clear()  # textChanged → 결과 '-'
        self.set_status(f"{FAIL_TEXT}: {detail}")

    def show_ocr_unavailable(self, message: str) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("한국어 OCR 필요")
        box.setText(message)
        box.open()  # 비모달: 이벤트 루프를 막지 않는다

    # ------------------------------------------------------------ 기록
    @Slot(bool)
    def _on_history_toggled(self, shown: bool) -> None:
        self.history_toggle.setText(("▾" if shown else "▸") + " 최근 기록")
        if shown:
            self._collapsed_size = self.size()
            self.history_table.setVisible(True)
            self.refresh_history()
            self.layout().activate()
            self.resize(max(self.width(), 420), max(self.height(), self.sizeHint().height()))
        else:
            self.history_table.setVisible(False)
            self.layout().activate()
            before = getattr(self, "_collapsed_size", self.size())
            self.resize(before.width(), max(before.height(), self.minimumSizeHint().height()))

    def refresh_history(self) -> None:
        if self.history_table.state() == QAbstractItemView.State.EditingState:
            self._history_dirty = True  # 편집이 끝나면 갱신한다
            return
        self._history_dirty = False
        try:
            rows = self.history.recent(RECENT_COUNT)
        except (OSError, ValueError, csv.Error) as exc:
            self.set_status(f"기록을 읽지 못했습니다: {exc}")
            rows = []
        self._filling_table = True
        try:
            self.history_table.setRowCount(len(rows))
            for r, row in enumerate(rows):
                values = [
                    row.time[11:16] if len(row.time) >= 16 else row.time,
                    row.item_name,
                    _fmt(row.price),
                    f"{row.party_size}인",
                    _fmt(row.recommended_bid),
                    "" if row.actual_price is None else _fmt(row.actual_price),
                ]
                for c, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.ItemDataRole.UserRole, row.index)
                    flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                    if c == self.COL_ACTUAL:
                        flags |= Qt.ItemFlag.ItemIsEditable
                    if c == self.COL_ITEM:
                        item.setToolTip(row.item_name)
                    item.setFlags(flags)
                    if c != self.COL_ITEM:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    self.history_table.setItem(r, c, item)
        finally:
            self._filling_table = False

    @Slot(QTableWidgetItem)
    def _on_history_item_changed(self, item: QTableWidgetItem) -> None:
        """표 칸 글자가 코드로 바뀐 경우(편집기를 거치지 않은 경우)."""
        if self._filling_table or item.column() != self.COL_ACTUAL:
            return
        self._commit_actual_price(int(item.data(Qt.ItemDataRole.UserRole)), item.text())

    def _commit_actual_price(self, history_index: int, text: str) -> None:
        text = text.replace(",", "").strip()
        if text and not text.isdigit():
            self.set_status("실제 낙찰가는 숫자만 입력하세요.")
        else:
            try:
                self.history.set_actual_price(history_index, int(text) if text else None)
            except (IndexError, ValueError, OSError, csv.Error) as exc:
                self.set_status(f"실제 낙찰가 저장 실패: {exc}")
            else:
                self.set_status("실제 낙찰가를 저장했습니다.")
        self.refresh_history()
        # 편집 중이라 미뤄졌다면, 편집기가 어떤 경로로 닫히든 이벤트 루프 다음 차례에 갱신한다.
        QMetaObject.invokeMethod(self, "_on_editor_closed", Qt.ConnectionType.QueuedConnection)

    @Slot()
    def _on_editor_closed(self) -> None:
        if self._history_dirty:
            self.refresh_history()

    # ------------------------------------------------------------ 설정/상태
    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def save_config(self) -> None:
        try:
            save_config(self.config, self.config_path)
        except OSError as exc:
            self.set_status(f"설정 저장 실패: {exc}")

    def _restore_position(self) -> None:
        pos = self.config.window_pos
        if not pos:
            return
        point = QPoint(pos[0], pos[1])
        if QGuiApplication.screenAt(point + QPoint(20, 20)) is not None:
            self.move(point)

    # ------------------------------------------------------------ 창 모양: 테두리 없는 창 / 아이콘 접기
    def paintEvent(self, event) -> None:  # noqa: N802
        """제목 표시줄 없는 반투명 창이라 배경(둥근 사각형 + 1px 테두리)을 직접 그린다."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor(WINDOW_BORDER), 1))
        p.setBrush(QColor(WINDOW_BG))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), WINDOW_RADIUS, WINDOW_RADIUS)
        p.end()

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    @Slot()
    def collapse(self) -> None:
        """창을 숨기고 작은 아이콘을 띄운다. 단축키는 계속 동작한다.

        아이콘 위치: 사용자가 아이콘을 끌어다 놓은 적이 있으면 그 자리(재시작 후에도 유지),
        없으면 창의 왼쪽 위.
        """
        if self._collapsed:
            return
        self._collapsed = True
        target = self._remembered_icon_pos() or self.pos()
        self.bubble.place(self._fit_on_screen(target, IconBubble.SIZE, IconBubble.SIZE))
        self.bubble.show()
        self.bubble.raise_()
        self.hide()

    @Slot()
    def expand(self) -> None:
        """아이콘 자리에서 원래 창을 다시 펼친다(화면 밖으로 나가지 않게 맞춘다)."""
        if not self._collapsed:
            return
        self._collapsed = False
        self.move(self._fit_on_screen(self.bubble.pos(), self.width(), self.height()))
        self.show()
        self.raise_()
        self.activateWindow()
        self.bubble.hide()
        self.save_config()  # 끌어다 놓은 아이콘 위치를 바로 저장

    @Slot(QPoint)
    def _on_icon_moved_by_user(self, pos: QPoint) -> None:
        if self._collapsed:
            self.config.icon_pos = [pos.x(), pos.y()]

    def _remembered_icon_pos(self) -> QPoint | None:
        """기억한 아이콘 위치. 모니터 구성이 바뀌어 화면 밖이면 None."""
        pos = self.config.icon_pos
        if not pos:
            return None
        point = QPoint(pos[0], pos[1])
        center = point + QPoint(IconBubble.SIZE // 2, IconBubble.SIZE // 2)
        return point if QGuiApplication.screenAt(center) is not None else None

    def _fit_on_screen(self, point: QPoint, width: int, height: int) -> QPoint:
        """width×height 크기 창의 왼쪽 위를 point 근처, 화면 안에 들어오는 자리로 맞춘다."""
        screen = (
            QGuiApplication.screenAt(point + QPoint(IconBubble.SIZE // 2, IconBubble.SIZE // 2))
            or self.screen()
            or QGuiApplication.primaryScreen()
        )
        if screen is None:
            return point
        area = screen.availableGeometry()
        x = max(area.left(), min(point.x(), area.right() - width + 1))
        y = max(area.top(), min(point.y(), area.bottom() - height + 1))
        return QPoint(x, y)

    @Slot()
    def exit_app(self) -> None:
        """✕ 버튼 / 아이콘 메뉴 '종료': 설정을 저장하고 단축키를 해제한 뒤 앱 종료를 요청한다."""
        self.close()
        self.exitRequested.emit()

    def closeEvent(self, event) -> None:  # noqa: N802
        # 창 위치와 아이콘 위치는 따로 저장한다(접힌 채로 종료해도 창은 마지막 창 자리에서 다시 열림).
        self.config.window_pos = [self.x(), self.y()]
        self.bubble.hide()
        self.save_config()
        self._teardown_hotkey()
        super().closeEvent(event)
