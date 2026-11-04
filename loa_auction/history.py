"""history.csv 읽기/쓰기.

- 인코딩 utf-8-sig (엑셀에서 한글이 깨지지 않도록 BOM 포함)
- 헤더: 시각,아이템명,거래소최저가,인원,추천입찰가,실제낙찰가
- 인식 성공 시 1행 추가. 같은 아이템·가격을 2초 이내에 다시 인식하면 추가하지 않는다.
- 실제낙찰가는 빈 값으로 시작하고, 입력하면 파일 전체를 다시 쓴다(다른 칸은 그대로 보존).
- 엑셀에서 "CSV(쉼표로 분리)"로 다시 저장해 cp949가 된 파일도 읽을 수 있고, 다음 기록 때 UTF-8로 바꾼다.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .config import app_dir

HISTORY_FILENAME = "history.csv"
ENCODING = "utf-8-sig"
FALLBACK_ENCODING = "cp949"
HEADER = ["시각", "아이템명", "거래소최저가", "인원", "추천입찰가", "실제낙찰가"]
COL_ACTUAL = 5
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
DUPLICATE_WINDOW = timedelta(seconds=2)


def default_history_path() -> Path:
    return app_dir() / HISTORY_FILENAME


def _to_int(text: str) -> int | None:
    text = (text or "").replace(",", "").strip()
    return int(text) if text.isdigit() else None


@dataclass(frozen=True)
class HistoryRow:
    index: int  # 파일 안 데이터 행 번호(0부터, 헤더 제외)
    cells: tuple[str, ...]  # 파일에 있던 칸 그대로(최소 6칸, 사용자가 덧붙인 칸 포함)

    @property
    def time(self) -> str:
        return self.cells[0]

    @property
    def item_name(self) -> str:
        return self.cells[1]

    @property
    def price(self) -> int:
        return _to_int(self.cells[2]) or 0

    @property
    def party_size(self) -> int:
        return _to_int(self.cells[3]) or 0

    @property
    def recommended_bid(self) -> int:
        return _to_int(self.cells[4]) or 0

    @property
    def actual_price(self) -> int | None:
        return _to_int(self.cells[COL_ACTUAL])


class HistoryStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else default_history_path()
        self._last: tuple[str, int, datetime] | None = None

    # -- 읽기 -------------------------------------------------------------
    def _read_text(self) -> tuple[str, str] | None:
        """(내용, 종류). 종류: "utf8" | "cp949"(엑셀 저장본) | "broken"(깨진 바이트 포함 UTF-8).
        파일이 없으면 None."""
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return None
        try:
            return raw.decode(ENCODING), "utf8"
        except UnicodeDecodeError:
            pass
        try:
            return raw.decode(FALLBACK_ENCODING), "cp949"  # 엄격하게 cp949로 읽힐 때만
        except UnicodeDecodeError:
            # 바이트 몇 개만 깨진 UTF-8: 보여 주기만 하고 자동 변환하지 않는다
            return raw.decode(ENCODING, errors="replace"), "broken"

    def _backup(self) -> None:
        backup = self.path.with_name(self.path.name + ".bak")
        backup.write_bytes(self.path.read_bytes())

    def _read(self) -> tuple[list[str] | None, list[HistoryRow]]:
        """(파일의 헤더 행 또는 None, 데이터 행들)."""
        read = self._read_text()
        if read is None:
            return None, []
        header: list[str] | None = None
        rows: list[HistoryRow] = []
        first = True
        for rec in csv.reader(io.StringIO(read[0])):
            if not any(c.strip() for c in rec):
                continue
            is_header = first and rec[0].lstrip("﻿").strip() == HEADER[0]
            first = False
            if is_header:
                header = rec
                continue
            cells = tuple(rec) + ("",) * (len(HEADER) - len(rec))
            rows.append(HistoryRow(index=len(rows), cells=cells))
        return header, rows

    def rows(self) -> list[HistoryRow]:
        return self._read()[1]

    def recent(self, n: int = 10) -> list[HistoryRow]:
        """최근 n건, 최신이 먼저."""
        return list(reversed(self.rows()[-n:])) if n > 0 else []

    # -- 쓰기 -------------------------------------------------------------
    def append(
        self,
        item_name: str,
        price: int,
        party_size: int,
        recommended_bid: int,
        now: datetime | None = None,
    ) -> bool:
        """1행 추가. 2초 이내 같은 아이템·가격 중복이면 추가하지 않고 False.

        파일을 쓸 수 없으면(엑셀에서 열려 있음 등) OSError를 그대로 낸다. 이때는 중복 판정
        기준도 갱신하지 않으므로 다시 시도하면 기록된다.
        """
        now = now or datetime.now()
        if self._last is not None:
            last_item, last_price, last_time = self._last
            if last_item == item_name and last_price == price and abs(now - last_time) <= DUPLICATE_WINDOW:
                return False

        row = [now.strftime(TIME_FORMAT), item_name, str(price), str(party_size), str(recommended_bid), ""]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        read = self._read_text()
        if read is not None and read[1] == "cp949":
            # 엑셀이 cp949로 저장한 파일: 원본을 .bak으로 남기고 UTF-8(BOM)로 바꾼 뒤 이어 쓴다.
            self._backup()
            header, rows = self._read()
            self._rewrite(header, rows)
        new_file = not self.path.exists() or self.path.stat().st_size == 0
        needs_newline = not new_file and not self._ends_with_newline()
        # 새 파일만 BOM(utf-8-sig)과 헤더를 쓰고, 이어 쓸 때는 BOM 없이 붙인다.
        with self.path.open("a", encoding=ENCODING if new_file else "utf-8", newline="") as f:
            if needs_newline:
                f.write("\r\n")
            writer = csv.writer(f)
            if new_file:
                writer.writerow(HEADER)
            writer.writerow(row)
        self._last = (item_name, price, now)
        return True

    def _ends_with_newline(self) -> bool:
        with self.path.open("rb") as f:
            f.seek(-1, 2)
            return f.read(1) in (b"\n", b"\r")

    def set_actual_price(self, index: int, actual_price: int | None) -> None:
        """index번째 데이터 행의 실제낙찰가만 바꾸고 파일을 다시 쓴다(다른 칸은 그대로)."""
        header, rows = self._read()
        if not 0 <= index < len(rows):
            raise IndexError(f"기록 행 번호가 범위를 벗어났습니다: {index}")
        if actual_price is not None and actual_price < 0:
            raise ValueError("실제 낙찰가는 0 이상이어야 합니다")
        old = rows[index]
        cells = list(old.cells)
        cells[COL_ACTUAL] = "" if actual_price is None else str(actual_price)
        rows[index] = HistoryRow(old.index, tuple(cells))
        read = self._read_text()
        if read is not None and read[1] != "utf8":
            self._backup()  # UTF-8이 아닌 원본은 다시 쓰기 전에 .bak으로 남긴다
        self._rewrite(header, rows)

    def _rewrite(self, header: list[str] | None, rows: list[HistoryRow]) -> None:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([c.lstrip("﻿") for c in header] if header else HEADER)
        for r in rows:
            writer.writerow(r.cells)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(buf.getvalue(), encoding=ENCODING, newline="")
        tmp.replace(self.path)
