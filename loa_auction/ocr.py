"""Windows 내장 OCR 래퍼와 "거래소 최저가" 파서.

흐름
1. 전체 화면 이미지를 OCR해서 줄(line)과 단어 bbox를 얻는다.
2. "최저가"를 포함한 라벨 줄을 찾는다.
3. 라벨과 수직으로 겹치는 같은 줄, 라벨 바로 오른쪽(일정 거리 안)의 숫자 토큰(`[\\d,]+`)을
   가격으로 쓴다.
4. 천 단위 쉼표가 없는 숫자는 확실하지 않은 값으로 본다(가격 옆 골드 아이콘이 숫자 한 글자로
   붙어 읽히는 경우가 있기 때문). 이때는 라벨 주변을 잘라 골드 아이콘(노란색)을 지우고
   2~3배 업스케일·그레이스케일·대비 보정한 뒤 다시 OCR해서 가격을 확정한다.
5. 아이템명은 라벨 바로 위에서 가장 가까운 텍스트 줄이며, 그 줄만 잘라 확대해 다시 읽는다.

같은 화면 하단의 보유 골드(예: 142,584)나 타이머(예: 02초)는 라벨과 같은 줄이 아니므로
가격 후보가 될 수 없다.
"""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence, TypeVar

from PIL import Image, ImageChops, ImageFilter, ImageOps

LABEL_KEYWORD = "최저가"
OCR_LANGUAGE = "ko"

KOREAN_OCR_HELP = (
    "Windows 한국어 OCR(광학 문자 인식)을 사용할 수 없습니다.\n"
    "설정 > 시간 및 언어 > 언어 및 지역 > 한국어 > 언어 옵션에서 "
    "'언어 팩'과 '광학 문자 인식'을 설치한 뒤 프로그램을 다시 실행하세요."
)

_NUMBER_RE = re.compile(r"^[\d,]+$")
_HANGUL_RE = re.compile(r"[가-힣]")
# OCR이 숫자를 비슷한 글자로 읽는 흔한 경우만 숫자로 되돌린다.
_DIGIT_LOOKALIKES = str.maketrans({"O": "0", "o": "0", "D": "0", "I": "1", "l": "1", "|": "1", ".": ","})

T = TypeVar("T")


class OcrUnavailableError(RuntimeError):
    """한국어 OCR 엔진을 만들 수 없을 때."""

    def __init__(self, message: str = KOREAN_OCR_HELP):
        super().__init__(message)


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    def vertical_overlap(self, other: "Box") -> float:
        return min(self.y1, other.y1) - max(self.y0, other.y0)

    def horizontal_overlap(self, other: "Box") -> float:
        return min(self.x1, other.x1) - max(self.x0, other.x0)

    @staticmethod
    def union(boxes: Iterable["Box"]) -> "Box":
        boxes = list(boxes)
        return Box(
            min(b.x0 for b in boxes),
            min(b.y0 for b in boxes),
            max(b.x1 for b in boxes),
            max(b.y1 for b in boxes),
        )


@dataclass(frozen=True)
class OcrWord:
    text: str
    box: Box


@dataclass(frozen=True)
class OcrLine:
    text: str
    words: tuple[OcrWord, ...]
    box: Box

    @staticmethod
    def from_words(words: Sequence[OcrWord]) -> "OcrLine":
        return OcrLine(" ".join(w.text for w in words), tuple(words), Box.union(w.box for w in words))


@dataclass(frozen=True)
class OcrResult:
    item_name: str
    price: int | None
    success: bool

    @staticmethod
    def failed(item_name: str = "") -> "OcrResult":
        return OcrResult(item_name=item_name, price=None, success=False)


# ---------------------------------------------------------------------------
# Windows OCR 호출
# ---------------------------------------------------------------------------

_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()


def _init_mta() -> None:
    try:
        import winrt.runtime as rt

        rt.init_apartment(rt.MTA)
    except Exception:
        pass  # winrt가 없거나 이미 초기화된 경우: 실제 호출 시점에 오류를 낸다


def _run_in_mta_thread(func: Callable[[], T]) -> T:
    """WinRT 호출을 프로세스 수명 동안 유지되는 전용 MTA 스레드 하나에서 실행한다.

    - 호출한 스레드가 Qt 메인 스레드(STA)여도 COM 아파트먼트를 바꾸지 않는다.
    - MTA가 계속 살아 있으므로 WinRT 객체가 나중에 다른 스레드에서 해제돼도 안전하다.
    - 작업이 한 줄로 직렬화되어 OCR 엔진을 동시에 쓰지 않는다.
    """
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="loa-ocr", initializer=_init_mta)
    return _executor.submit(func).result()


def _create_engine():
    from winrt.windows.globalization import Language
    from winrt.windows.media.ocr import OcrEngine

    lang = Language(OCR_LANGUAGE)
    if not OcrEngine.is_language_supported(lang):
        raise OcrUnavailableError()
    engine = OcrEngine.try_create_from_language(lang)
    if engine is None:
        raise OcrUnavailableError()
    return engine, int(OcrEngine.max_image_dimension)


def check_ocr_available() -> None:
    """한국어 OCR을 쓸 수 없으면 OcrUnavailableError를 낸다."""

    def probe() -> None:
        _create_engine()  # 엔진은 MTA 스레드 안에서 만들고 버린다

    try:
        _run_in_mta_thread(probe)
    except OcrUnavailableError:
        raise
    except Exception as exc:  # winrt 모듈 누락 등
        raise OcrUnavailableError(f"{KOREAN_OCR_HELP}\n(상세: {exc})") from exc


def _recognize_sync(img: Image.Image) -> list[OcrLine]:
    from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap

    engine, max_dim = _create_engine()
    img = img.convert("RGBA")
    scale = 1.0
    if max(img.size) > max_dim:
        scale = max_dim / max(img.size)
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)
    w, h = img.size
    bitmap = SoftwareBitmap.create_copy_from_buffer(
        img.tobytes("raw", "BGRA"), BitmapPixelFormat.BGRA8, w, h, BitmapAlphaMode.PREMULTIPLIED
    )
    # MTA 스레드에서 비동기 작업이 끝날 때까지 기다린다(asyncio 이벤트 루프를 쓰지 않는다).
    result = engine.recognize_async(bitmap).get()
    lines: list[OcrLine] = []
    for line in result.lines:
        words = []
        for word in line.words:
            r = word.bounding_rect
            words.append(
                OcrWord(
                    word.text,
                    Box(r.x / scale, r.y / scale, (r.x + r.width) / scale, (r.y + r.height) / scale),
                )
            )
        if words:
            lines.append(OcrLine.from_words(words))
    return lines


def recognize_lines(img: Image.Image) -> list[OcrLine]:
    """PIL 이미지를 Windows OCR(한국어)로 읽어 줄 목록을 돌려준다. 좌표는 원본 이미지 기준."""
    try:
        return _run_in_mta_thread(lambda: _recognize_sync(img))
    except ImportError as exc:  # winrt 패키지가 빠진 경우
        raise OcrUnavailableError(f"{KOREAN_OCR_HELP}\n(상세: {exc})") from exc


# ---------------------------------------------------------------------------
# 파서 (OCR 결과 → 아이템명/가격). OCR 호출 없이 단독으로도 쓸 수 있다.
# ---------------------------------------------------------------------------


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


# 저해상도에서 "최저가"의 '저'가 비슷한 글자로 읽히는 경우까지 허용한다.
_LABEL_RE = re.compile(r"최[저자제져적]가")
# 라벨만 있는 줄: "거래소 최저가" (뒤에 가격 숫자가 같은 줄로 붙어 읽힐 수 있음)
_PURE_LABEL_RE = re.compile(r"^(거래소)?최[저자제져적]가[\d,.]*$")


def _is_label_text(compact: str) -> bool:
    if LABEL_KEYWORD in compact:
        return True
    # 오인식 허용은 "거래소"가 함께 읽힌 경우로 한정한다 ("최고가" 같은 다른 라벨 오탐 방지).
    return "거래소" in compact and _LABEL_RE.search(compact) is not None


def find_label_lines(lines: Sequence[OcrLine]) -> list[OcrLine]:
    """"최저가" 라벨 줄. "거래소"까지 함께 읽힌 줄이 있으면 그 줄만 쓴다.

    라벨 글자만 있는 줄(경매 창의 실제 라벨)을 채팅처럼 다른 글자가 섞인 줄보다 앞에 둔다.
    """
    found = [ln for ln in lines if _is_label_text(_compact(ln.text))]
    with_exchange = [ln for ln in found if "거래소" in _compact(ln.text)]
    chosen = with_exchange or found
    return sorted(chosen, key=lambda ln: 0 if _PURE_LABEL_RE.match(_compact(ln.text)) else 1)


def _label_box(line: OcrLine) -> Box:
    """줄 안에서 라벨 단어들(… "최저가"까지)의 bbox."""
    acc = ""
    for i, word in enumerate(line.words):
        acc += word.text
        if _LABEL_RE.search(acc):
            return Box.union(w.box for w in line.words[: i + 1])
    return line.box


def _price_search_width(label: Box) -> float:
    """라벨 오른쪽 끝에서 가격을 찾을 최대 거리. 경매 창 밖(채팅, 다른 창)의 숫자를 배제한다."""
    return max(label.width * 3.5, label.height * 14)


def _normalize_number_token(text: str) -> str | None:
    t = text.strip()
    if not t or not any(ch.isdigit() for ch in t):
        return None
    t = t.translate(_DIGIT_LOOKALIKES)
    return t if _NUMBER_RE.match(t) else None


def _parse_int(token: str) -> int | None:
    token = token.strip(",")
    digits = token.replace(",", "")
    if not digits.isdigit():
        return None
    groups = token.split(",")
    # 천 단위 쉼표 형식 검사: "17,500" OK, "1,75,00" 같은 깨진 값은 버린다.
    if len(groups) > 1 and (not groups[0] or len(groups[0]) > 3 or any(len(g) != 3 for g in groups[1:])):
        return None
    return int(digits)


# 가격 바로 오른쪽의 골드 아이콘이 숫자 한 글자(대개 0)로 붙어 읽힌 경우: "17,5000" → "17,500"
_TRAILING_ICON_RE = re.compile(r"^(\d{1,3}(?:,\d{3})+)\d$")


@dataclass(frozen=True)
class PriceRead:
    value: int
    # 천 단위 쉼표가 있는 값이면 True. 쉼표가 없으면 골드 아이콘이 숫자로 붙었을 수 있어 확정하지 않는다.
    confident: bool


def _assemble_number(tokens: Sequence[str]) -> PriceRead | None:
    """인접한 숫자 토큰들을 하나의 가격으로 해석한다. 해석할 수 없으면 None."""
    if len(tokens) == 1:
        candidates = [tokens[0]]
    else:
        candidates = []
        # "17," + "500" 처럼 쉼표 경계로 쪼개진 경우만 그대로 붙인다.
        if all(a.endswith(",") or b.startswith(",") for a, b in zip(tokens, tokens[1:])):
            candidates.append("".join(tokens))
        # 쉼표가 공백으로 읽힌 경우: "17" "500" → "17,500"
        candidates.append(",".join(t.strip(",") for t in tokens))
    for cand in candidates:
        core = cand.strip(",")
        value = _parse_int(core)
        if value is not None and (len(tokens) == 1 or "," in core):
            return PriceRead(value, confident="," in core)
        m = _TRAILING_ICON_RE.match(core)
        if m:
            return PriceRead(int(m.group(1).replace(",", "")), confident=True)
    return None


def _words_right_of(label: Box, lines: Sequence[OcrLine]) -> list[OcrWord]:
    """라벨과 수직으로 겹치고 라벨 오른쪽 일정 거리 안에 있는 단어들 (x순)."""
    max_x = label.x1 + _price_search_width(label)
    out = []
    for line in lines:
        for word in line.words:
            if word.box.x0 < label.x1 - label.height * 0.2 or word.box.x0 > max_x:
                continue
            overlap = label.vertical_overlap(word.box)
            if overlap <= 0 or overlap < min(label.height, word.box.height) * 0.4:
                continue
            out.append(word)
    return sorted(out, key=lambda w: w.box.x0)


def read_price_right_of(label: Box, lines: Sequence[OcrLine]) -> PriceRead | None:
    """라벨 오른쪽 같은 줄에서 가장 가까운 숫자 덩어리를 가격으로 해석한다."""
    words = _words_right_of(label, lines)
    for i, word in enumerate(words):
        if not any(ch.isdigit() for ch in word.text):
            continue  # 아이콘·기호 등
        token = _normalize_number_token(word.text)
        if token is None:
            return None  # 라벨 오른쪽 첫 숫자가 깨져 있으면 추측하지 않고 재인식에 맡긴다
        # "17," "500"처럼 쪼개진 숫자는 가까운 간격이면 한 덩어리로 본다.
        group, last, end = [token], word.box, i + 1
        for nxt in words[i + 1 :]:
            nt = _normalize_number_token(nxt.text)
            if nt is None or nxt.box.x0 - last.x1 > label.height * 0.8:
                break
            group.append(nt)
            last, end = nxt.box, end + 1
        # 바로 뒤가 "초"면 타이머다.
        if end < len(words) and words[end].text.startswith("초"):
            return None
        return _assemble_number(group)
    return None


def price_right_of(label: Box, lines: Sequence[OcrLine]) -> int | None:
    """확실한(천 단위 쉼표가 있는) 가격만 돌려준다."""
    read = read_price_right_of(label, lines)
    return read.value if read and read.confident else None


def _clean_item_name(text: str) -> str:
    tokens = text.split()
    # 아이콘 테두리가 "1", "|" 같은 글자로 읽히는 경우를 앞뒤에서 제거한다.
    while tokens and not _HANGUL_RE.search(tokens[0]) and len(tokens[0]) <= 2:
        tokens.pop(0)
    while tokens and not _HANGUL_RE.search(tokens[-1]) and len(tokens[-1]) <= 2:
        tokens.pop()
    return " ".join(tokens)


def item_name_above(label_line: OcrLine, label: Box, lines: Sequence[OcrLine]) -> str:
    """라벨 바로 위에서 가장 가까운 텍스트 줄의 글자. 없으면 ""."""
    line = item_line_above(label_line, label, lines)
    return _clean_item_name(line.text) if line else ""


def item_line_above(label_line: OcrLine, label: Box, lines: Sequence[OcrLine]) -> OcrLine | None:
    """라벨 바로 위에서 가장 가까운 텍스트 줄."""
    best: tuple[float, OcrLine] | None = None
    region = Box(label.x0 - label.width, label.y0, label.x1 + label.width * 3, label.y1)
    for line in lines:
        if line is label_line:
            continue
        b = line.box
        if b.cy >= label.y0:  # 라벨보다 위에 있어야 한다
            continue
        gap = label.y0 - b.y1
        if gap > label.height * 4:
            continue
        # 라벨 영역(라벨~가격 사이)과 수평으로 겹쳐야 한다
        if region.horizontal_overlap(b) <= 0:
            continue
        if not _clean_item_name(line.text):
            continue
        if best is None or gap < best[0]:
            best = (gap, line)
    return best[1] if best else None


@dataclass(frozen=True)
class LabelMatch:
    line: OcrLine
    label: Box
    price_read: PriceRead | None
    item_line: OcrLine | None

    @property
    def price(self) -> int | None:
        """확실한 가격(천 단위 쉼표 포함)만. 쉼표 없는 값은 재인식으로 확정한다."""
        r = self.price_read
        return r.value if r and r.confident else None

    @property
    def item_name(self) -> str:
        return _clean_item_name(self.item_line.text) if self.item_line else ""


def parse_lines(lines: Sequence[OcrLine]) -> list[LabelMatch]:
    """OCR 줄 목록에서 라벨 후보마다 (가격, 아이템명 줄)을 뽑는다.

    경매 창의 실제 라벨일 가능성이 높은 순서: 라벨 글자만 있는 줄, 위에 아이템명 줄이 있는 경우.
    """
    out = []
    for line in find_label_lines(lines):
        label = _label_box(line)
        out.append(LabelMatch(line, label, read_price_right_of(label, lines), item_line_above(line, label, lines)))
    pure = lambda m: 0 if _PURE_LABEL_RE.match(_compact(m.line.text)) else 1  # noqa: E731
    return sorted(out, key=lambda m: (pure(m), 0 if m.item_line else 1))


# ---------------------------------------------------------------------------
# 라벨/아이템명 주변 재인식
# ---------------------------------------------------------------------------

Recognizer = Callable[[Image.Image], list[OcrLine]]


def _mask_gold_icon(img: Image.Image) -> Image.Image:
    """노란색(골드 아이콘) 픽셀을 검게 지운다. 가격 숫자는 흰색이라 남는다."""
    r, g, b = img.convert("RGB").split()
    yellow_r = ImageChops.subtract(r, b).point(lambda v: 255 if v > 80 else 0)
    yellow_g = ImageChops.subtract(g, b).point(lambda v: 255 if v > 50 else 0)
    mask = ImageChops.multiply(yellow_r, yellow_g).filter(ImageFilter.MaxFilter(5))
    out = img.convert("RGB")
    out.paste((0, 0, 0), mask=mask)
    return out


def _crop_variants(
    img: Image.Image, label: Box, mask_icon: bool
) -> list[tuple[Image.Image, float, float, float]]:
    """라벨 줄 주변을 잘라 업스케일한 이미지들. (이미지, 원본 x 오프셋, y 오프셋, 배율)"""
    lh = max(label.height, 4.0)
    x0 = max(0, int(label.x0 - lh))
    x1 = min(img.width, int(label.x1 + _price_search_width(label)))
    y0 = max(0, int(label.y0 - lh * 1.2))
    y1 = min(img.height, int(label.y1 + lh * 1.2))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return []
    crop = img.crop((x0, y0, x1, y1))
    if mask_icon:
        crop = _mask_gold_icon(crop)
    crop = crop.convert("L")
    # 글자 높이가 약 40px가 되도록 키운다 (2~3배 범위 제한)
    scale = min(3.0, max(2.0, 40.0 / lh))
    size = (int(crop.width * scale), int(crop.height * scale))
    gray = ImageOps.autocontrast(crop.resize(size, Image.LANCZOS), cutoff=1)
    inverted = ImageOps.invert(gray)  # 어두운 배경의 밝은 글자 → 흰 바탕 검은 글자
    return [(inverted, x0, y0, scale), (gray, x0, y0, scale)]


def _map_back(lines: Sequence[OcrLine], ox: float, oy: float, scale: float) -> list[OcrLine]:
    def m(b: Box) -> Box:
        return Box(ox + b.x0 / scale, oy + b.y0 / scale, ox + b.x1 / scale, oy + b.y1 / scale)

    return [OcrLine.from_words([OcrWord(w.text, m(w.box)) for w in ln.words]) for ln in lines]


def _reread_price(img: Image.Image, match: LabelMatch, recognize: Recognizer, mask_icon: bool) -> PriceRead | None:
    for variant, ox, oy, scale in _crop_variants(img, match.label, mask_icon):
        crop_lines = _map_back(recognize(variant), ox, oy, scale)
        for m in parse_lines(crop_lines):
            if m.price_read is not None:
                return m.price_read
    return None


def _masked_agrees(masked: int, unmasked: Sequence[int]) -> bool:
    """아이콘을 지운 값이 지우지 않은 값과 같거나, 그 뒤에 아이콘 숫자 한 글자만 더 붙은 관계인가."""
    m = str(masked)
    return any(str(u).startswith(m) and len(str(u)) - len(m) <= 1 for u in unmasked)


def _resolve_price(img: Image.Image, match: LabelMatch, recognize: Recognizer) -> int | None:
    """전체 화면에서 읽은 값이 확실하면 그대로, 아니면 라벨 주변을 다시 읽어 확정한다.

    틀린 가격보다 인식 실패가 낫다는 원칙으로, 확정할 수 없으면 None을 돌려준다.
    """
    if match.price is not None:
        return match.price
    # 1) 확대 재인식(아이콘 그대로): 쉼표가 있는 값이면 확정
    plain = _reread_price(img, match, recognize, mask_icon=False)
    if plain is not None and plain.confident:
        return plain.value
    # 2) 골드 아이콘(노란색)을 지운 재인식. 가격 글자 자체가 노란색/주황색이면 글자까지 지워져
    #    엉뚱한 값이 될 수 있으므로, 지우지 않은 판독값과 맞아떨어질 때만 받는다.
    masked = _reread_price(img, match, recognize, mask_icon=True)
    if masked is None:
        return None
    unmasked = [r.value for r in (match.price_read, plain) if r is not None]
    if unmasked:
        return masked.value if _masked_agrees(masked.value, unmasked) else None
    return masked.value if masked.confident else None


def _refine_item_name(img: Image.Image, match: LabelMatch, recognize: Recognizer) -> str:
    """아이템명 줄을 잘라 글자 높이 약 40px로 키운 뒤 다시 읽는다. 실패하면 원래 값."""
    original = match.item_name
    if match.item_line is None:
        return original
    b, lh = match.item_line.box, max(match.label.height, 4.0)
    x0, x1 = max(0, int(b.x0 - lh)), min(img.width, int(b.x1 + lh))
    y0, y1 = max(0, int(b.y0 - lh * 0.6)), min(img.height, int(b.y1 + lh * 0.6))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return original
    crop = img.crop((x0, y0, x1, y1)).convert("L")
    scale = max(1.0, 40.0 / lh)
    if scale > 1.0:
        crop = crop.resize((int(crop.width * scale), int(crop.height * scale)), Image.LANCZOS)
    lines = _map_back(recognize(ImageOps.autocontrast(crop, cutoff=1)), x0, y0, scale)
    candidates = [
        ln
        for ln in lines
        if _HANGUL_RE.search(ln.text) and b.y0 <= ln.box.cy <= b.y1 and not _is_label_text(_compact(ln.text))
    ]
    if not candidates:
        return original
    best = min(candidates, key=lambda ln: abs(ln.box.cy - b.cy))
    return _clean_item_name(best.text) or original


# 전체 화면 OCR 배율. 작은 화면은 글자가 작아 라벨을 못 찾을 수 있으므로 키워 가며 다시 읽는다.
_FULL_PASS_FACTORS = (1, 2, 3)
_MAX_UPSCALED_SIDE = 8000


def _matches_at(img: Image.Image, factor: int, recognize: Recognizer) -> list[LabelMatch]:
    if factor == 1:
        return parse_lines(recognize(img))
    big = img.resize((img.width * factor, img.height * factor), Image.LANCZOS)
    return parse_lines(_map_back(recognize(big), 0, 0, float(factor)))


def parse_image(img: Image.Image, recognize: Recognizer = recognize_lines) -> OcrResult:
    """전체 화면 이미지에서 아이템명과 거래소 최저가를 읽는다."""
    img = img.convert("RGB")
    item_fallback = ""
    for factor in _FULL_PASS_FACTORS:
        if factor > 1 and max(img.size) * factor > _MAX_UPSCALED_SIDE:
            break
        for match in _matches_at(img, factor, recognize):
            item_fallback = item_fallback or match.item_name
            price = _resolve_price(img, match, recognize)
            if price is not None:
                return OcrResult(item_name=_refine_item_name(img, match, recognize), price=price, success=True)
    return OcrResult.failed(item_fallback)


def recognize(img: Image.Image) -> OcrResult:
    """UI/selftest용 진입점. 한국어 OCR이 없으면 OcrUnavailableError."""
    return parse_image(img)
