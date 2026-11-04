# fixtures

테스트 이미지는 저장소에 포함되지 않습니다. 레이드 전리품 경매 화면을 캡처해 닉네임을 가린 뒤 `sample_<이름>_<거래소최저가>.png` 형식(예: `sample_mychar_7776.png`)으로 넣으면 `pytest tests/test_ocr.py`가 자동으로 검증합니다.

- 확장자: `.png`, `.jpg`, `.webp`
- 파일명 끝의 숫자가 기대 가격입니다. 예) `sample_kakao_17500.png` → 17,500
- 이미지가 하나도 없으면 OCR 테스트는 건너뛰지 않고 **실패**합니다.
- `build.ps1`은 이 폴더의 `sample_*` 이미지를 exe에 함께 넣어 `--selftest`에서 OCR까지 검사합니다. 이미지가 없으면 계산만 검사합니다.
- 아이템명까지 검사하려면 `tests/conftest.py`의 `EXPECTED_ITEM_SUBSTRING`에 파일명과 아이템명 일부를 추가하세요. 목록에 없으면 아이템명이 비어 있지 않은지만 확인합니다.
