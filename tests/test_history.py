import csv
from datetime import datetime, timedelta

import pytest

from loa_auction.history import HEADER, HistoryStore

T0 = datetime(2026, 11, 4, 21, 30, 0)


@pytest.fixture
def store(tmp_path):
    return HistoryStore(tmp_path / "history.csv")


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f))


def test_append_creates_file_with_bom_and_header(store):
    assert store.append("유물 전율의 각인서", 7776, 4, 5036, now=T0) is True
    raw = store.path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert raw.count(b"\xef\xbb\xbf") == 1
    rows = read_csv(store.path)
    assert rows[0] == HEADER == ["시각", "아이템명", "거래소최저가", "인원", "추천입찰가", "실제낙찰가"]
    assert rows[1] == ["2026-11-04 21:30:00", "유물 전율의 각인서", "7776", "4", "5036", ""]


def test_append_more_rows_keeps_single_bom(store):
    for i in range(3):
        store.append(f"아이템{i}", 1000 + i, 8, 700 + i, now=T0 + timedelta(seconds=10 * i))
    raw = store.path.read_bytes()
    assert raw.count(b"\xef\xbb\xbf") == 1
    assert len(read_csv(store.path)) == 4


def test_duplicate_within_two_seconds_is_ignored(store):
    assert store.append("전율", 7776, 4, 5036, now=T0) is True
    assert store.append("전율", 7776, 4, 5036, now=T0 + timedelta(seconds=1)) is False
    assert store.append("전율", 7776, 8, 5876, now=T0 + timedelta(seconds=2)) is False
    assert len(store.rows()) == 1
    # 2초가 지나면 새 기록
    assert store.append("전율", 7776, 4, 5036, now=T0 + timedelta(seconds=3)) is True
    assert len(store.rows()) == 2


def test_different_price_or_item_is_not_duplicate(store):
    assert store.append("전율", 7776, 4, 5036, now=T0)
    assert store.append("전율", 7777, 4, 5037, now=T0 + timedelta(milliseconds=500))
    assert store.append("결투", 7777, 4, 5037, now=T0 + timedelta(milliseconds=900))
    assert len(store.rows()) == 3


def test_set_actual_price_rewrites_row(store):
    store.append("A", 1000, 4, 647, now=T0)
    store.append("B", 2000, 8, 1511, now=T0 + timedelta(seconds=5))
    store.append("C", 3000, 4, 1943, now=T0 + timedelta(seconds=10))
    store.set_actual_price(1, 1450)
    rows = read_csv(store.path)
    assert rows[2] == [rows[2][0], "B", "2000", "8", "1511", "1450"]
    assert rows[1][5] == "" and rows[3][5] == ""
    assert store.path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert store.rows()[1].actual_price == 1450
    # 빈 값으로 되돌리기
    store.set_actual_price(1, None)
    assert store.rows()[1].actual_price is None
    with pytest.raises(IndexError):
        store.set_actual_price(3, 10)


def test_recent_returns_last_ten_newest_first(store):
    for i in range(13):
        store.append(f"아이템{i}", 100 * (i + 1), 4, i, now=T0 + timedelta(seconds=5 * i))
    recent = store.recent()
    assert len(recent) == 10
    assert [r.item_name for r in recent] == [f"아이템{i}" for i in range(12, 2, -1)]
    assert recent[0].index == 12
    store.set_actual_price(recent[0].index, 999)
    assert store.recent()[0].actual_price == 999
    assert store.rows()[12].actual_price == 999


def test_missing_file_reads_empty(store):
    assert store.rows() == []
    assert store.recent() == []


def test_failed_write_raises_and_does_not_count_as_duplicate(tmp_path):
    blocked = tmp_path / "history.csv"
    blocked.mkdir()  # 폴더라서 파일로 열 수 없다(엑셀 잠금 같은 OSError)
    store = HistoryStore(blocked)
    with pytest.raises(OSError):
        store.append("전율", 7776, 4, 5036, now=T0)
    blocked.rmdir()
    # 실패한 기록은 중복 판정에 쓰이지 않으므로 1초 뒤 다시 시도하면 기록된다
    assert store.append("전율", 7776, 4, 5036, now=T0 + timedelta(seconds=1)) is True
    assert len(store.rows()) == 1


def test_cp949_file_is_read_and_converted_on_append(store):
    store.path.write_bytes(
        "시각,아이템명,거래소최저가,인원,추천입찰가,실제낙찰가\r\n2026-11-04 20:00:00,전율 각인서,7776,4,5036,5000\r\n".encode("cp949")
    )
    [row] = store.rows()
    assert (row.item_name, row.price, row.actual_price) == ("전율 각인서", 7776, 5000)
    original = store.path.read_bytes()
    store.append("결투 각인서", 17500, 4, 11335, now=T0)
    raw = store.path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    raw.decode("utf-8-sig")  # 전체가 UTF-8이어야 한다
    assert [r.item_name for r in store.rows()] == ["전율 각인서", "결투 각인서"]
    assert (store.path.parent / "history.csv.bak").read_bytes() == original  # 변환 전 원본 백업


def test_utf8_file_with_one_broken_byte_is_not_garbled(store):
    good = "시각,아이템명,거래소최저가,인원,추천입찰가,실제낙찰가\r\n2026-11-04 20:00:00,유물 전율 각인서,7776,4,5036,\r\n"
    store.path.write_bytes(b"\xef\xbb\xbf" + good.encode("utf-8") + b"2026-11-04 20:01:00,\xff,1,4,0,\r\n")
    assert store.rows()[0].item_name == "유물 전율 각인서"
    store.append("결투", 17500, 4, 11335, now=T0)
    rows = store.rows()
    assert [r.item_name for r in rows][0::2] == ["유물 전율 각인서", "결투"]
    raw = store.path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf" + good.encode("utf-8"))  # 앞부분은 그대로
    store.set_actual_price(0, 5000)
    assert store.rows()[0].item_name == "유물 전율 각인서"
    assert (store.path.parent / "history.csv.bak").exists()


def test_rewrite_preserves_user_cells_and_header(store):
    store.path.write_text(
        "시각,아이템명,거래소최저가,인원,추천입찰가,실제낙찰가,메모\r\n"
        "2026-11-04 20:00:00,전율,7776,4,5036,,첫 판\r\n"
        "2026-11-04 20:01:00,결투,abc,8,,,\r\n",
        encoding="utf-8-sig",
        newline="",
    )
    store.set_actual_price(0, 5100)
    rows = read_csv(store.path)
    assert rows[0] == ["시각", "아이템명", "거래소최저가", "인원", "추천입찰가", "실제낙찰가", "메모"]
    assert rows[1] == ["2026-11-04 20:00:00", "전율", "7776", "4", "5036", "5100", "첫 판"]
    assert rows[2] == ["2026-11-04 20:01:00", "결투", "abc", "8", "", "", ""]


def test_append_to_file_without_trailing_newline(store):
    store.path.write_text("시각,아이템명,거래소최저가,인원,추천입찰가,실제낙찰가\r\n2026-11-04 20:00:00,전율,7776,4,5036,", encoding="utf-8-sig")
    store.append("결투", 17500, 4, 11335, now=T0)
    assert [r.item_name for r in store.rows()] == ["전율", "결투"]


def test_existing_file_from_previous_session_is_appended(store):
    store.append("A", 1000, 4, 647, now=T0)
    again = HistoryStore(store.path)
    again.append("A", 1000, 4, 647, now=T0 + timedelta(seconds=1))
    assert [r.item_name for r in again.rows()] == ["A", "A"]
    assert store.path.read_bytes().count(b"\xef\xbb\xbf") == 1
