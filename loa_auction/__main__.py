"""진입점.

    python -m loa_auction            미니 창 실행
    python -m loa_auction --selftest 계산·OCR 자체 검사 (exit 0 = 통과)

PyInstaller가 이 파일을 스크립트로 실행하므로 절대 import를 쓴다.
"""

from __future__ import annotations

import argparse
import ctypes
import re
import sys
from pathlib import Path

from loa_auction.config import app_dir, bundle_dir

SELFTEST_LOG = "selftest.log"
FIXTURE_RE = re.compile(r"^sample_.+_(\d+)\.(webp|png|jpg)$", re.IGNORECASE)
CALC_CASES = {
    (7776, 4): (5540, 5036, 5832),
    (7776, 8): (6463, 5876, 6804),
    (0, 4): (0, 0, 0),
}


def find_fixtures() -> list[Path]:
    seen: dict[str, Path] = {}
    for folder in (bundle_dir() / "fixtures", app_dir() / "fixtures"):
        if folder.is_dir():
            for p in sorted(folder.iterdir()):
                if FIXTURE_RE.match(p.name):
                    seen.setdefault(p.name, p)
    return list(seen.values())


def _console_stream():
    """--windowed 빌드에는 콘솔이 없으므로 부모 콘솔(실행한 터미널)에 붙어서 출력한다."""
    if sys.stdout is not None:
        return sys.stdout
    try:
        kernel32 = ctypes.windll.kernel32
        if not kernel32.AttachConsole(-1):  # ATTACH_PARENT_PROCESS
            return None
        codepage = kernel32.GetConsoleOutputCP() or 65001
        return open("CONOUT$", "w", encoding=f"cp{codepage}", errors="replace")
    except Exception:
        return None


def run_selftest() -> int:
    lines: list[str] = []
    ok = True

    def log(text: str) -> None:
        lines.append(text)

    from loa_auction.calc import calculate

    for (price, party), expected in CALC_CASES.items():
        got = tuple(calculate(price, party))
        passed = got == expected
        ok &= passed
        log(f"[{'OK' if passed else 'FAIL'}] calc({price}, {party}) = {got} (기대 {expected})")
    try:
        calculate(7776, 5)
    except ValueError:
        log("[OK] calc(7776, 5) → ValueError")
    else:
        ok = False
        log("[FAIL] calc(7776, 5)가 ValueError를 내지 않음")

    fixtures = find_fixtures()
    if not fixtures:
        log("OCR 샘플 없음 (fixtures/sample_<이름>_<가격>.png 를 넣고 다시 빌드하면 OCR도 검사합니다)")
    else:
        from PIL import Image

        from loa_auction import ocr

        try:
            ocr.check_ocr_available()
        except ocr.OcrUnavailableError as exc:
            ok = False
            log(f"[FAIL] {exc}")
        else:
            for path in fixtures:
                expected = int(FIXTURE_RE.match(path.name).group(1))
                try:
                    with Image.open(path) as im:
                        result = ocr.recognize(im)
                except Exception as exc:  # noqa: BLE001
                    ok = False
                    log(f"[FAIL] OCR {path.name}: {type(exc).__name__}: {exc}")
                    continue
                passed = result.success and result.price == expected
                ok &= passed
                log(
                    f"[{'OK' if passed else 'FAIL'}] OCR {path.name}: price={result.price} "
                    f"(기대 {expected}), item={result.item_name!r}"
                )

    log("SELFTEST PASS" if ok else "SELFTEST FAIL")
    text = "\n".join(lines) + "\n"
    try:
        (app_dir() / SELFTEST_LOG).write_text(text, encoding="utf-8-sig")
    except OSError:
        pass
    stream = _console_stream()
    if stream is not None:
        try:
            stream.write(text)
            stream.flush()
        except Exception:
            pass
    return 0 if ok else 1


def run_app() -> int:
    from PySide6.QtWidgets import QApplication

    from loa_auction import ocr
    from loa_auction.config import default_config_path, load_config_checked
    from loa_auction.history import HistoryStore
    from loa_auction.ui import MiniWindow, app_icon

    app = QApplication(sys.argv)
    app.setApplicationName("LoaAuctionCalc")
    app.setWindowIcon(app_icon())  # 안내 창(QMessageBox) 등에도 같은 아이콘
    config_path = default_config_path()
    config, warning = load_config_checked(config_path)
    window = MiniWindow(config, config_path, HistoryStore())
    if warning:
        window.set_status(warning)
    window.show()
    try:
        ocr.check_ocr_available()
    except ocr.OcrUnavailableError as exc:
        window.show_failure("한국어 OCR을 사용할 수 없습니다.")
        window.show_ocr_unavailable(str(exc))
    return app.exec()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="LoaAuctionCalc", add_help=True)
    parser.add_argument("--selftest", action="store_true", help="계산·OCR 자체 검사 후 종료")
    args, _unknown = parser.parse_known_args(argv)
    if args.selftest:
        return run_selftest()
    return run_app()


if __name__ == "__main__":
    sys.exit(main())
