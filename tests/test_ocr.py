"""OCR 테스트.

- fixture 이미지 테스트는 실제 Windows OCR을 호출한다(모킹 없음).
- 파서 단위 테스트는 OCR 엔진 없이 bbox 규칙(같은 줄, 오른쪽, 거리 제한, 타이머/보유 골드 배제)만 검증한다.
"""

from __future__ import annotations

import pytest
from conftest import (
    EXPECTED_ITEM_SUBSTRING,
    NO_FIXTURE_MESSAGE,
    expected_price,
    fixture_images,
    fixture_params,
    require_fixture,
)
from PIL import Image, ImageDraw

from loa_auction import ocr
from loa_auction.ocr import Box, OcrLine, OcrResult, OcrUnavailableError, OcrWord, PriceRead

FIXTURES = fixture_images()


def _scale_cases():
    cases = []
    for path in FIXTURES:
        with Image.open(path) as im:
            width = im.width
        cases.append(pytest.param(path, 1.0, id=f"{path.name}-x1.0"))
        # 다른 해상도로 찍은 전체 화면을 흉내 낸다 (폭 1600~4000px 범위만)
        for scale in (0.6, 0.75, 1.25):
            if 1500 <= width * scale <= 4000:
                cases.append(pytest.param(path, scale, id=f"{path.name}-x{scale}"))
    return cases or [pytest.param(None, 1.0, id="no-fixture-images")]


def test_fixture_images_exist():
    assert FIXTURES, NO_FIXTURE_MESSAGE


def test_korean_ocr_available():
    ocr.check_ocr_available()


@pytest.mark.parametrize("path", fixture_params())
def test_fixture_price_and_item(path):
    path = require_fixture(path)
    with Image.open(path) as im:
        result = ocr.recognize(im)
    assert isinstance(result, OcrResult)
    assert result.success is True
    assert result.price == expected_price(path)
    expected_item = EXPECTED_ITEM_SUBSTRING.get(path.name)
    if expected_item:
        assert expected_item in result.item_name
    else:
        assert result.item_name.strip()


@pytest.mark.parametrize(("path", "scale"), _scale_cases())
def test_fixture_price_at_other_resolutions(path, scale):
    path = require_fixture(path)
    with Image.open(path) as im:
        im = im.convert("RGB")
        if scale != 1.0:
            im = im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
        result = ocr.recognize(im)
    assert result.price == expected_price(path)
    assert result.success


def _label_of(im: Image.Image) -> Box:
    matches = ocr.parse_lines(ocr.recognize_lines(im))
    assert matches, "fixture에서 '최저가' 라벨을 찾지 못했습니다"
    return matches[0].label


@pytest.mark.parametrize("path", fixture_params())
def test_fixture_without_price_does_not_pick_other_numbers(path):
    """가격 칸만 지운 실제 화면: 보유 골드·타이머, 그리고 같은 줄 멀리 있는 숫자를 가격으로 잡으면 안 된다."""
    path = require_fixture(path)
    with Image.open(path) as im:
        im = im.convert("RGB")
    label = _label_of(im)
    row = (int(label.x1 + 2), int(label.y0 - label.height), int(label.x1 + label.width * 4), int(label.y1 + label.height))
    # 같은 줄 오른쪽 끝(경매 창 밖)에 가격 그림을 그대로 복사해 둔다
    patch = im.crop(row)
    far_x = im.width - patch.width - 10
    assert far_x > row[2] + label.width * 2
    im.paste(patch, (far_x, row[1]))
    ImageDraw.Draw(im).rectangle(row, fill=(0, 0, 0))  # 원래 가격 + 골드 아이콘 지우기
    result = ocr.recognize(im)
    assert result.success is False
    assert result.price is None


@pytest.mark.parametrize("path", fixture_params())
def test_fixture_comma_less_price_next_to_gold_icon(path):
    """'17,500'에서 '17,'을 지워 실제 가격이 500인 화면: 골드 아이콘이 0으로 붙어 5000이 되면 안 된다."""
    path = require_fixture(path)
    with Image.open(path) as im:
        im = im.convert("RGB")
    lines = ocr.recognize_lines(im)
    label = _label_of(im)
    price_words = [w for w in ocr._words_right_of(label, lines) if any(c.isdigit() for c in w.text)]
    assert price_words
    word = price_words[0]
    if "," not in word.text or len(word.text.replace(",", "")) < 4:
        pytest.fail(f"이 검사는 천 단위 쉼표가 있는 4자리 이상 가격 fixture가 필요합니다: {word.text!r}")
    # 마지막 쉼표 그룹(3자리)만 남기고 앞부분을 배경색으로 덮는다
    head, tail = word.text.rsplit(",", 1)
    cut_x = word.box.x0 + word.box.width * (len(head) + 1) / len(word.text)
    bg = im.getpixel((int(label.x1 + 5), int(label.cy)))
    ImageDraw.Draw(im).rectangle((word.box.x0 - 2, word.box.y0 - 2, cut_x, word.box.y1 + 2), fill=bg)
    prices = {}
    for scale in (1.0, 0.75, 0.6):
        scaled = im if scale == 1.0 else im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
        prices[scale] = ocr.recognize(scaled).price
    # 틀린 값(예: 5000)은 절대 안 되고, 적어도 한 해상도에서는 500을 읽어야 한다
    assert all(p in (None, int(tail)) for p in prices.values()), prices
    assert int(tail) in prices.values(), prices


def _recolor_text(im: Image.Image, box: Box, color: tuple[int, int, int]) -> None:
    """box 안의 밝은 흰 글자 픽셀을 color로 바꾼다(가격 글자가 노란색/주황색인 화면 흉내)."""
    px = im.load()
    for x in range(int(box.x0) - 1, int(box.x1) + 1):
        for y in range(int(box.y0) - 1, int(box.y1) + 1):
            r, g, b = px[x, y]
            if min(r, g, b) > 110 and max(r, g, b) - min(r, g, b) < 40:
                k = min(r, g, b) / 255
                px[x, y] = tuple(int(c * k) for c in color)


@pytest.mark.parametrize("color", [(245, 150, 40), (240, 205, 60)], ids=["orange", "yellow"])
@pytest.mark.parametrize("path", fixture_params())
def test_fixture_colored_price_is_never_misread(path, color):
    """가격 글자 자체가 노란색/주황색이면 아이콘 지우기가 글자까지 지운다: 틀린 값 대신 실패여야 한다."""
    path = require_fixture(path)
    with Image.open(path) as im:
        im = im.convert("RGB")
    label = _label_of(im)
    word = next(w for w in ocr._words_right_of(label, ocr.recognize_lines(im)) if any(c.isdigit() for c in w.text))
    _recolor_text(im, word.box, color)
    expected = expected_price(path)
    for scale in (1.0, 0.85, 0.7, 0.5):
        scaled = im if scale == 1.0 else im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
        result = ocr.recognize(scaled)
        assert result.price in (None, expected), (scale, result)


@pytest.mark.parametrize(
    ("masked", "unmasked", "ok"),
    [
        (500, [5000], True),  # 아이콘이 0으로 붙은 판독
        (500, [5004], True),
        (500, [500], True),
        (4, [500], False),  # 주황색 글자가 지워져 남은 조각
        (7500, [1], False),
        (500, [50000], False),
    ],
)
def test_masked_read_must_agree_with_unmasked(masked, unmasked, ok):
    assert ocr._masked_agrees(masked, unmasked) is ok


def test_blank_image_fails():
    result = ocr.recognize(Image.new("RGB", (1920, 1080), (10, 10, 20)))
    assert result == OcrResult(item_name="", price=None, success=False)


def test_missing_language_raises_korean_message(monkeypatch):
    from winrt.windows.media.ocr import OcrEngine

    installed = {lang.language_tag.split("-")[0] for lang in OcrEngine.available_recognizer_languages}
    missing = next(tag for tag in ("th", "ar", "he", "hi", "fi", "el", "sw", "is") if tag not in installed)
    monkeypatch.setattr(ocr, "OCR_LANGUAGE", missing)
    with pytest.raises(OcrUnavailableError) as excinfo:
        ocr.recognize(Image.new("RGB", (64, 64)))
    message = str(excinfo.value)
    assert "한국어" in message
    assert "광학 문자 인식" in message
    assert "시간 및 언어" in message


def test_gold_icon_mask_removes_yellow_keeps_white():
    img = Image.new("RGB", (40, 10), (18, 19, 26))
    for x in range(0, 10):
        img.putpixel((x, 5), (235, 235, 235))  # 흰 숫자
    for x in range(30, 40):
        img.putpixel((x, 5), (240, 190, 60))  # 골드 아이콘
    out = ocr._mask_gold_icon(img)
    assert out.getpixel((5, 5)) == (235, 235, 235)
    assert out.getpixel((35, 5)) == (0, 0, 0)


# ---------------------------------------------------------------------------
# 파서 단위 테스트 (OCR 엔진 없이 bbox 규칙만 검증)
# ---------------------------------------------------------------------------


def line(*words: tuple[str, float, float, float, float]) -> OcrLine:
    return OcrLine.from_words([OcrWord(t, Box(x0, y0, x1, y1)) for t, x0, y0, x1, y1 in words])


LABEL_LINE = line(("거래소", 445, 490, 490, 506), ("최저가", 495, 490, 540, 506))
PRICE_LINE = line(("7,776", 678, 493, 736, 513))
AUCTION_SCREEN = [
    line(("전리품", 491, 226, 535, 245), ("경매", 540, 226, 585, 245)),
    line(("1", 404, 456, 410, 484), ("유물", 420, 456, 460, 484), ("전율의", 466, 456, 520, 484), ("각인서", 530, 456, 600, 484)),
    LABEL_LINE,
    PRICE_LINE,
    line(("경매", 446, 709, 480, 727), ("진행까지", 485, 709, 560, 727)),
    line(("02초", 510, 742, 569, 766)),
    line(("소지금", 321, 915, 373, 933)),
    line(("142,584", 645, 915, 730, 938)),
]


def without_price(*extra: OcrLine) -> list[OcrLine]:
    return [ln for ln in AUCTION_SCREEN if ln is not PRICE_LINE] + list(extra)


def test_parser_reads_price_and_item_from_same_row():
    [m] = ocr.parse_lines(AUCTION_SCREEN)
    assert m.price == 7776
    assert m.price_read == PriceRead(7776, confident=True)
    assert m.item_name == "유물 전율의 각인서"


def test_parser_ignores_gold_and_timer_when_price_missing():
    [m] = ocr.parse_lines(without_price())
    assert m.price is None and m.price_read is None


def test_parser_ignores_number_left_of_label():
    assert ocr.parse_lines(without_price(line(("9,999", 300, 490, 360, 506))))[0].price_read is None


def test_parser_ignores_number_far_right_on_same_row():
    far = line(("1,680,238", 2300, 492, 2400, 512))
    assert ocr.parse_lines(without_price(far))[0].price_read is None
    # 가격이 있으면 가까운 가격을 쓴다
    assert ocr.parse_lines(AUCTION_SCREEN + [far])[0].price == 7776


def test_parser_does_not_skip_broken_first_number():
    broken = line(("7,7?6", 678, 493, 736, 513))
    later = line(("9,999", 760, 493, 800, 513))
    assert ocr.parse_lines(without_price(broken, later))[0].price_read is None


def test_parser_rejects_timer_on_label_row():
    timer = line(("02", 678, 493, 700, 513), ("초", 702, 493, 715, 513))
    assert ocr.parse_lines(without_price(timer))[0].price_read is None


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        (["7,776"], PriceRead(7776, True)),
        (["17,", "500"], PriceRead(17500, True)),
        (["17", ",500"], PriceRead(17500, True)),
        (["17", "500"], PriceRead(17500, True)),  # 쉼표가 공백으로 읽힘
        (["17,5000"], PriceRead(17500, True)),  # 골드 아이콘이 0으로 붙어 읽힘
        (["17,5004"], PriceRead(17500, True)),
        (["17", "5000"], PriceRead(17500, True)),
        (["1,680,238"], PriceRead(1680238, True)),
        (["7,776O"], PriceRead(7776, True)),
        # 쉼표 없는 값은 아이콘이 붙었을 수 있어 확정하지 않는다(재인식으로 확정)
        (["7776"], PriceRead(7776, False)),
        (["5000"], PriceRead(5000, False)),
        (["500"], PriceRead(500, False)),
        (["1,7,500"], None),
    ],
)
def test_parser_number_assembly(tokens, expected):
    words, x = [], 678.0
    for t in tokens:
        words.append((t, x, 493, x + 10 * len(t), 513))
        x += 10 * len(t) + 4
    m = ocr.parse_lines(without_price(line(*words)))[0]
    assert m.price_read == expected
    assert m.price == (expected.value if expected and expected.confident else None)


def test_parser_accepts_misread_label_only_with_exchange_word():
    misread = [line(("거래소", 445, 490, 490, 506), ("최자가", 495, 490, 540, 506)), PRICE_LINE]
    assert ocr.parse_lines(misread)[0].price == 7776
    other_label = [line(("현재", 445, 490, 490, 506), ("최고가", 495, 490, 540, 506)), line(("0", 678, 493, 690, 513))]
    assert ocr.parse_lines(other_label) == []


def test_parser_prefers_exchange_label_line():
    screen = AUCTION_SCREEN + [line(("최저가", 445, 600, 490, 616)), line(("1,000", 678, 600, 736, 616))]
    matches = ocr.parse_lines(screen)
    assert len(matches) == 1 and matches[0].price == 7776


def test_parser_prefers_pure_label_over_chat_line():
    chat = line(("[파티]", 20, 1000, 70, 1016), ("거래소", 75, 1000, 120, 1016), ("최저가", 125, 1000, 170, 1016), ("15,000", 180, 1000, 230, 1016))
    matches = ocr.parse_lines([chat] + AUCTION_SCREEN)
    assert matches[0].line is LABEL_LINE and matches[0].price == 7776


def test_parser_item_name_empty_when_nothing_above():
    [m] = ocr.parse_lines([LABEL_LINE, PRICE_LINE])
    assert m.item_name == ""


# ---------------------------------------------------------------------------
# 재검사 순서(parse_image) 단위 테스트: 가짜 OCR이 이미지 크기에 따라 정해진 줄을 돌려준다
# ---------------------------------------------------------------------------

W, H = 1000, 600
ITEM_LINE = line(("유물", 150, 100, 180, 120), ("전율", 185, 100, 215, 120))
REAL_LABEL = line(("거래소", 150, 130, 180, 142), ("최저가", 183, 130, 210, 142))
DECOY_LABEL = line(("거래소", 800, 500, 840, 512), ("최저가", 844, 500, 880, 512))  # 위에 아이템명 줄 없음


def scaled_lines(lines, factor):
    return [line(*[(w.text, w.box.x0 * factor, w.box.y0 * factor, w.box.x1 * factor, w.box.y1 * factor) for w in ln.words]) for ln in lines]


def test_decoy_label_does_not_block_whole_screen_rescan():
    """1배에서는 다른 곳의 '최저가'(미끼)만 보이고 진짜 라벨은 2배에서야 보이는 작은 화면."""
    calls = []

    def fake_ocr(img):
        calls.append(img.size)
        if img.size == (W, H):
            return [DECOY_LABEL]
        if img.size == (W * 2, H * 2):
            return scaled_lines([ITEM_LINE, REAL_LABEL, line(("7,776", 280, 131, 320, 142)), DECOY_LABEL], 2)
        return []

    result = ocr.parse_image(Image.new("RGB", (W, H)), recognize=fake_ocr)
    assert result.price == 7776 and result.success
    assert (W * 2, H * 2) in calls  # 미끼 주변만 보지 않고 화면 전체를 2배로 다시 읽었다


def test_found_label_rescans_only_its_panel_region():
    """진짜 라벨(위에 아이템명 줄)은 찾았는데 가격이 안 읽힐 때: 화면 전체가 아니라 라벨 주변만 키워 읽는다."""
    calls = []

    def fake_ocr(img):
        calls.append(img.size)
        return [ITEM_LINE, REAL_LABEL] if img.size == (W, H) else []

    img = Image.new("RGB", (W, H))
    result = ocr.parse_image(img, recognize=fake_ocr)
    assert result.success is False and result.item_name == "유물 전율"
    assert (W * 2, H * 2) not in calls and (W * 3, H * 3) not in calls
    x0, y0, x1, y1 = ocr._panel_region(img, ocr._label_box(REAL_LABEL))
    assert ((x1 - x0) * 2, (y1 - y0) * 2) in calls and ((x1 - x0) * 3, (y1 - y0) * 3) in calls


def test_numbers_cut_by_region_edge_are_dropped():
    lines = [
        line(("1,680", 950, 10, 1000, 30)),  # 오른쪽 가장자리에 걸림: "1,680,238"의 일부일 수 있다
        line(("7,776", 500, 10, 560, 30)),
        line(("거래소", 0, 40, 40, 60), ("최저가", 44, 40, 90, 60)),  # 글자 단어는 가장자리에 걸려도 남긴다
    ]
    kept = ocr._drop_cut_numbers(lines, 1000, 100)
    assert [ln.text for ln in kept] == ["7,776", "거래소 최저가"]


def test_number_cut_in_gap_before_right_edge_is_dropped():
    # 글자 사이 빈칸에서 잘리면 숫자 상자가 가장자리에서 몇 px 떨어져 있다(높이 20 → 12px까지 잘린 것으로 본다)
    gap_cut = [line(("1,680", 930, 10, 990, 30))]
    assert ocr._drop_cut_numbers(gap_cut, 1000, 100) == []


def test_numbers_at_real_image_edge_are_kept():
    # 영역의 오른쪽이 화면 끝이면(잘라 낸 곳이 아니면) 그 자리의 숫자는 온전하다
    lines = [line(("17,500", 940, 10, 1000, 30))]
    kept = ocr._drop_cut_numbers(lines, 1000, 100, cut=(True, True, False, True))
    assert [ln.text for ln in kept] == ["17,500"]


def test_region_read_drops_number_cut_at_crop_edge():
    """라벨 주변 재검사에서 영역 오른쪽 끝에 걸린 숫자("1,680,238"의 앞부분)를 가격으로 쓰면 안 된다."""
    img = Image.new("RGB", (W, H))
    x0, y0, x1, y1 = ocr._panel_region(img, ocr._label_box(REAL_LABEL))
    rw, rh = (x1 - x0) * 2, (y1 - y0) * 2

    def fake_ocr(part):
        if part.size == (W, H):
            return [ITEM_LINE, REAL_LABEL]  # 1배: 진짜 라벨은 있는데 가격이 안 읽힘
        if part.size == (rw, rh):  # 라벨 주변 2배: 영역 오른쪽 끝에 잘린 숫자가 보임
            shifted = [line(*[(w.text, (w.box.x0 - x0) * 2, (w.box.y0 - y0) * 2, (w.box.x1 - x0) * 2, (w.box.y1 - y0) * 2) for w in ln.words]) for ln in (ITEM_LINE, REAL_LABEL)]
            label_top, label_bottom = (130 - y0) * 2, (142 - y0) * 2
            return shifted + [line(("1,680", rw - 110, label_top + 2, rw, label_bottom))]
        return []

    result = ocr.parse_image(img, recognize=fake_ocr)
    assert result.success is False and result.price is None


def test_chat_line_with_text_above_cannot_narrow_the_search():
    """'[파티] … 거래소 최저가'처럼 라벨 글자만 있는 줄이 아니면, 위에 글자가 있어도 범위를 좁히지 않는다."""
    chat_above = line(("어제", 780, 470, 820, 488), ("경매", 825, 470, 865, 488))
    chat = line(("[파티]", 740, 500, 795, 512), ("거래소", 800, 500, 840, 512), ("최저가", 844, 500, 880, 512))
    calls = []

    def fake_ocr(img):
        calls.append(img.size)
        if img.size == (W, H):
            return [chat_above, chat]
        if img.size == (W * 2, H * 2):
            return scaled_lines([ITEM_LINE, REAL_LABEL, line(("7,776", 280, 131, 320, 142)), chat_above, chat], 2)
        return []

    result = ocr.parse_image(Image.new("RGB", (W, H)), recognize=fake_ocr)
    assert result.price == 7776
    assert (W * 2, H * 2) in calls


def test_non_winrt_import_error_is_not_reported_as_missing_korean_ocr(monkeypatch):
    def broken_parse(img, recognize=None):
        raise ImportError("No module named 'some_codec'", name="some_codec")

    monkeypatch.setattr(ocr, "parse_image", broken_parse)
    with pytest.raises(ImportError):
        ocr.recognize(Image.new("RGB", (10, 10)))


def test_winrt_import_error_is_reported_as_missing_korean_ocr(monkeypatch):
    def missing_winrt():
        raise ImportError("DLL load failed while importing _winrt", name="winrt._winrt")

    monkeypatch.setattr(ocr, "_create_engine", missing_winrt)
    with pytest.raises(OcrUnavailableError) as excinfo:
        ocr.recognize(Image.new("RGB", (10, 10)))
    assert "광학 문자 인식" in str(excinfo.value)


@pytest.mark.parametrize("path", fixture_params())
def test_decoy_label_elsewhere_does_not_change_result(path):
    """실제 화면에 '거래소 최저가' 글자(미끼)가 하나 더 보여도 결과가 바뀌면 안 된다(작은 해상도 포함)."""
    path = require_fixture(path)
    with Image.open(path) as im:
        im = im.convert("RGB")
    label = _label_of(im)
    decoy = im.crop((int(label.x0) - 4, int(label.y0) - 4, int(label.x1) + 4, int(label.y1) + 4))
    for scale in (1.0, 0.5):
        base = im if scale == 1.0 else im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
        with_decoy = base.copy()
        with_decoy.paste(decoy, (int(base.width * 0.70), int(base.height * 0.85)))
        assert ocr.recognize(with_decoy).price == ocr.recognize(base).price, scale
