import pytest

from loa_auction.calc import CalcResult, calculate


@pytest.mark.parametrize(
    ("price", "party", "expected"),
    [
        (7776, 4, (5540, 5036, 5832)),
        (7776, 8, (6463, 5876, 6804)),
        (0, 4, (0, 0, 0)),
        (0, 8, (0, 0, 0)),
        (17500, 4, (12468, 11335, 13125)),
        (17500, 8, (14546, 13224, 15312)),
    ],
)
def test_calculate_values(price, party, expected):
    result = calculate(price, party)
    assert isinstance(result, CalcResult)
    assert tuple(result) == expected
    assert result.sell_breakeven == expected[0]
    assert result.recommended_bid == expected[1]
    assert result.use_breakeven == expected[2]


def test_floor_only_once_at_the_end():
    # 7776×0.95×3/4 = 5540.4 → 중간에 내리면 5540/1.1 = 5036.36,
    # 끝에 한 번 내리면 5540.4/1.1 = 5036.72. 둘 다 5036이지만
    # 중간 내림이 결과를 바꾸는 값(예: 101, 4)을 검증한다.
    # 101×0.95×3/4 = 71.9625 → /1.1 = 65.42 → 65 (중간 내림 시 71/1.1 = 64.5 → 64)
    assert calculate(101, 4).recommended_bid == 65


def test_no_float_error_on_large_price():
    # 1,000,000 × 0.95 × 7/8 = 831,250 정확히 정수
    assert calculate(1_000_000, 8).sell_breakeven == 831_250


@pytest.mark.parametrize("party", [0, 1, 2, 3, 5, 6, 7, 9, 16, True])
def test_invalid_party_size(party):
    with pytest.raises(ValueError):
        calculate(7776, party)


def test_n5_raises():
    with pytest.raises(ValueError):
        calculate(7776, 5)


@pytest.mark.parametrize("price", [-1, -7776])
def test_negative_price(price):
    with pytest.raises(ValueError):
        calculate(price, 4)


@pytest.mark.parametrize("price", [7776.0, "7776", None])
def test_non_int_price(price):
    with pytest.raises(ValueError):
        calculate(price, 4)
